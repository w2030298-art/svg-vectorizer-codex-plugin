from __future__ import annotations

import json
import math
import os
import re
import subprocess
import xml.etree.ElementTree as ET
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import vtracer
from PIL import Image, ImageDraw
from skimage.metrics import structural_similarity


QUALITY_PROFILES = {
    "compact": {
        "filter_speckle": 16,
        "color_precision": 5,
        "layer_difference": 24,
        "corner_threshold": 70,
        "length_threshold": 8.0,
        "path_precision": 2,
    },
    "balanced": {
        "filter_speckle": 8,
        "color_precision": 6,
        "layer_difference": 16,
        "corner_threshold": 60,
        "length_threshold": 4.0,
        "path_precision": 3,
    },
    "fidelity": {
        "filter_speckle": 2,
        "color_precision": 7,
        "layer_difference": 8,
        "corner_threshold": 50,
        "length_threshold": 2.0,
        "path_precision": 3,
    },
}


def _path(value: str | Path) -> Path:
    return Path(value).expanduser().resolve()


def _safe_stem(path: Path) -> str:
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", path.stem).strip("._")
    return stem or "trace"


def _hex(rgb: tuple[int, int, int]) -> str:
    return f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"


def _load_rgba(path: Path) -> np.ndarray:
    return np.asarray(Image.open(path).convert("RGBA"), dtype=np.uint8)


def _load_rgb(path: Path) -> np.ndarray:
    return np.asarray(Image.open(path).convert("RGB"), dtype=np.uint8)


def _estimate_background_rgb(rgb: np.ndarray) -> tuple[int, int, int]:
    border = np.concatenate((rgb[0, :, :], rgb[-1, :, :], rgb[:, 0, :], rgb[:, -1, :]), axis=0)
    return tuple(int(round(channel)) for channel in np.median(border, axis=0))


def _flood_background(candidate: np.ndarray) -> np.ndarray:
    height, width = candidate.shape
    background = np.zeros((height, width), dtype=bool)
    queue: deque[tuple[int, int]] = deque()

    def add(x: int, y: int) -> None:
        if candidate[y, x] and not background[y, x]:
            background[y, x] = True
            queue.append((x, y))

    for x in range(width):
        add(x, 0)
        add(x, height - 1)
    for y in range(height):
        add(0, y)
        add(width - 1, y)

    while queue:
        x, y = queue.popleft()
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if 0 <= nx < width and 0 <= ny < height:
                add(nx, ny)
    return background


def _keep_components(mask: np.ndarray, min_area: int, must_touch: np.ndarray | None = None) -> np.ndarray:
    labels, stats = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)[1:3]
    kept = np.zeros_like(mask, dtype=bool)
    for label_id in range(1, stats.shape[0]):
        area = int(stats[label_id, cv2.CC_STAT_AREA])
        if area < min_area:
            continue
        component = labels == label_id
        if must_touch is not None and not np.logical_and(component, must_touch).any():
            continue
        kept[component] = True
    return kept


def _foreground_mask(rgb: np.ndarray, rgba: np.ndarray | None, mask_mode: str) -> np.ndarray:
    if mask_mode == "none":
        return np.ones(rgb.shape[:2], dtype=bool)

    if mask_mode in {"auto", "alpha"} and rgba is not None and rgba[:, :, 3].min() < 255:
        return rgba[:, :, 3] > 0

    if mask_mode == "warm-icon":
        return _warm_icon_mask(rgb)

    bg = _estimate_background_rgb(rgb)
    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB).astype(np.float32)
    bg_lab = cv2.cvtColor(np.array([[bg]], dtype=np.uint8), cv2.COLOR_RGB2LAB)[0, 0].astype(np.float32)
    distance = np.linalg.norm(lab - bg_lab, axis=2)
    background = _flood_background(distance <= 24)
    return ~background


def _warm_icon_mask(rgb: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    hue = hsv[:, :, 0]
    saturation = hsv[:, :, 1]
    value = hsv[:, :, 2]
    height, width = hue.shape
    warm_hue = (hue >= 3) & (hue <= 32)
    orange = warm_hue & (saturation >= 95) & (value >= 105)
    orange = _keep_components(orange, min_area=max(20, (height * width) // 5000))

    outline_radius = max(3, round(min(width, height) * 0.045))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (outline_radius * 2 + 1, outline_radius * 2 + 1))
    near_orange = cv2.dilate(orange.astype(np.uint8), kernel) > 0
    dark = (value <= 72) & (rgb.max(axis=2) <= 95)
    foreground = orange | (dark & near_orange)

    edge_radius = max(1, round(min(width, height) * 0.006))
    edge_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (edge_radius * 2 + 1, edge_radius * 2 + 1))
    near_foreground = cv2.dilate(foreground.astype(np.uint8), edge_kernel) > 0
    foreground |= warm_hue & (saturation >= 45) & (value >= 45) & near_foreground
    foreground = _keep_components(foreground, min_area=max(20, (height * width) // 7000), must_touch=orange)
    return cv2.morphologyEx(foreground.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8)) > 0


def prepare_transparent_png(input_path: str | Path, output_path: str | Path, mask_mode: str) -> dict[str, Any]:
    input_path = _path(input_path)
    output_path = _path(output_path)
    rgba_source = _load_rgba(input_path)
    rgb = rgba_source[:, :, :3]
    effective_mode = "alpha" if mask_mode == "auto" and rgba_source[:, :, 3].min() < 255 else mask_mode
    if effective_mode == "auto":
        effective_mode = "flood"
    foreground = _foreground_mask(rgb, rgba_source, effective_mode)

    rgba = np.zeros((rgb.shape[0], rgb.shape[1], 4), dtype=np.uint8)
    rgba[foreground, :3] = rgb[foreground]
    rgba[foreground, 3] = 255
    output_path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgba).save(output_path)
    return {
        "prepared_png": str(output_path),
        "mask_mode": effective_mode,
        "width": int(rgb.shape[1]),
        "height": int(rgb.shape[0]),
        "foreground_pixels": int(foreground.sum()),
    }


def _svg_stats(svg_path: Path) -> dict[str, Any]:
    tree = ET.parse(svg_path)
    root = tree.getroot()
    namespace = root.tag.split("}", 1)[0] + "}" if root.tag.startswith("{") else ""
    paths = root.findall(f".//{namespace}path")
    fills: dict[str, int] = {}
    for path in paths:
        fill = path.attrib.get("fill")
        style = path.attrib.get("style", "")
        if fill is None and "fill:" in style:
            fill = style.split("fill:", 1)[1].split(";", 1)[0].strip()
        if fill:
            fills[fill] = fills.get(fill, 0) + 1
    return {
        "svg_width": root.attrib.get("width"),
        "svg_height": root.attrib.get("height"),
        "viewBox": root.attrib.get("viewBox"),
        "path_count": len(paths),
        "fill_count": len(fills),
        "fills": fills,
        "svg_bytes": svg_path.stat().st_size,
    }


def _rect_path(x: int, y: int, width: int) -> str:
    return f"M{x} {y}h{width}v1H{x}z"


def _write_pixel_svg(prepared_png: Path, svg_path: Path) -> dict[str, Any]:
    rgba = _load_rgba(prepared_png)
    height, width = rgba.shape[:2]
    paths_by_color: dict[tuple[int, int, int, int], list[str]] = defaultdict(list)
    for y in range(height):
        x = 0
        while x < width:
            if rgba[y, x, 3] == 0:
                x += 1
                continue
            color = tuple(int(v) for v in rgba[y, x])
            start = x
            x += 1
            while x < width and rgba[y, x, 3] > 0 and tuple(int(v) for v in rgba[y, x]) == color:
                x += 1
            paths_by_color[color].append(_rect_path(start, y, x - start))

    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" shape-rendering="crispEdges" role="img" aria-label="Pixel fidelity SVG trace">',
        "  <title>Pixel fidelity SVG trace</title>",
    ]
    for (r, g, b, a), paths in sorted(paths_by_color.items()):
        opacity = "" if a == 255 else f' fill-opacity="{a / 255:.3f}"'
        lines.append(f'  <path fill="#{r:02x}{g:02x}{b:02x}"{opacity} d="{"".join(paths)}"/>')
    lines.append("</svg>")
    svg_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return _svg_stats(svg_path)


def convert_image_to_svg(
    input_path: str | Path,
    output_dir: str | Path,
    mode: str = "vtracer",
    mask_mode: str = "auto",
    quality_profile: str = "balanced",
    name: str | None = None,
) -> dict[str, Any]:
    input_path = _path(input_path)
    output_dir = _path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = name or _safe_stem(input_path)
    prepared_png = output_dir / f"{stem}_prepared.png"
    prep = prepare_transparent_png(input_path, prepared_png, mask_mode)

    if mode not in {"vtracer", "pixel"}:
        raise ValueError("mode must be 'vtracer' or 'pixel'")
    if quality_profile not in QUALITY_PROFILES:
        raise ValueError(f"unknown quality_profile: {quality_profile}")

    svg_path = output_dir / f"{stem}_{mode}.svg"
    if mode == "pixel":
        stats = _write_pixel_svg(prepared_png, svg_path)
    else:
        settings = QUALITY_PROFILES[quality_profile]
        vtracer.convert_image_to_svg_py(
            str(prepared_png),
            str(svg_path),
            colormode="color",
            hierarchical="stacked",
            mode="spline",
            max_iterations=10,
            splice_threshold=45,
            **settings,
        )
        stats = _svg_stats(svg_path)

    result = {
        "mode": mode,
        "input": str(input_path),
        "svg": str(svg_path),
        "prepared_png": str(prepared_png),
        "quality_profile": quality_profile,
        **prep,
        **stats,
    }
    manifest = output_dir / f"{stem}_{mode}_manifest.json"
    manifest.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    result["manifest"] = str(manifest)
    return result


def _composite_on_checker(rgba: np.ndarray) -> Image.Image:
    height, width = rgba.shape[:2]
    yy, xx = np.indices((height, width))
    checker = (((xx // 12) + (yy // 12)) % 2)[:, :, None]
    board = np.where(checker == 0, 235, 205).astype(np.uint8)
    board = np.repeat(board, 3, axis=2).astype(np.float32)
    alpha = rgba[:, :, 3:4].astype(np.float32) / 255.0
    rgb = rgba[:, :, :3].astype(np.float32)
    return Image.fromarray((rgb * alpha + board * (1 - alpha)).astype(np.uint8))


def _diff_contact_sheet(source_rgba: np.ndarray, rendered_rgba: np.ndarray, output_path: Path) -> None:
    diff = np.abs(source_rgba.astype(np.int16) - rendered_rgba.astype(np.int16)).astype(np.uint8)
    diff_rgb = np.clip(diff[:, :, :3].astype(np.int16) * 3 + diff[:, :, 3:4].astype(np.int16), 0, 255).astype(np.uint8)
    images = [_composite_on_checker(source_rgba), _composite_on_checker(rendered_rgba), Image.fromarray(diff_rgb)]
    labels = ["source", "rendered/proxy", "difference x3"]
    scale = max(1, min(6, 420 // max(source_rgba.shape[:2])))
    panels = []
    for label, image in zip(labels, images):
        scaled = image.resize((image.width * scale, image.height * scale), Image.Resampling.NEAREST)
        panel = Image.new("RGB", (scaled.width, scaled.height + 24), "white")
        panel.paste(scaled, (0, 24))
        ImageDraw.Draw(panel).text((4, 5), label, fill=(20, 20, 20))
        panels.append(panel)
    gap = 12
    canvas = Image.new("RGB", (sum(p.width for p in panels) + gap * 2, max(p.height for p in panels)), "white")
    x = 0
    for panel in panels:
        canvas.paste(panel, (x, 0))
        x += panel.width + gap
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path)


def _metrics(source_rgba: np.ndarray, rendered_rgba: np.ndarray) -> dict[str, Any]:
    source_alpha = source_rgba[:, :, 3] > 0
    rendered_alpha = rendered_rgba[:, :, 3] > 0
    union = source_alpha | rendered_alpha
    intersection = source_alpha & rendered_alpha
    alpha_iou = float(intersection.sum() / union.sum()) if union.any() else 1.0
    ssim = float(structural_similarity(source_rgba, rendered_rgba, channel_axis=-1, data_range=255))
    return {
        "source_foreground_pixels": int(source_alpha.sum()),
        "rendered_foreground_pixels": int(rendered_alpha.sum()),
        "alpha_iou": alpha_iou,
        "rgba_ssim": ssim,
        "mean_abs_rgba_delta": float(np.abs(source_rgba.astype(np.int16) - rendered_rgba.astype(np.int16)).mean()),
    }


def _render_svg(svg_path: Path, output_png: Path, width: int, height: int) -> tuple[np.ndarray | None, str, str | None]:
    helper = os.environ.get("SVG_VECTORIZER_RENDER_HELPER")
    node_modules = os.environ.get("SVG_VECTORIZER_NODE_MODULES")
    setup_error = os.environ.get("SVG_VECTORIZER_RENDER_SETUP_ERROR")
    if not helper or not node_modules:
        return None, "prepared-png-proxy", setup_error or "SVG raster renderer is unavailable."

    env = os.environ.copy()
    env["NODE_PATH"] = node_modules
    command = ["node", helper, str(svg_path), str(output_png), str(width), str(height)]
    try:
        result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=60, check=False)
    except Exception as exc:
        return None, "prepared-png-proxy", f"SVG renderer failed to start: {exc}"
    if result.returncode != 0:
        return None, "prepared-png-proxy", (result.stderr or result.stdout or "SVG renderer failed.").strip()
    return _load_rgba(output_png), "resvg-js", None


def validate_svg_trace(
    source_image_path: str | Path,
    svg_path: str | Path,
    output_dir: str | Path,
    prepared_png_path: str | Path | None = None,
) -> dict[str, Any]:
    source_image_path = _path(source_image_path)
    svg_path = _path(svg_path)
    output_dir = _path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    prepared_png = _path(prepared_png_path) if prepared_png_path else output_dir / "source_prepared.png"
    if prepared_png_path is None:
        prepare_transparent_png(source_image_path, prepared_png, "auto")

    source_rgba = _load_rgba(prepared_png)
    rendered_png = output_dir / f"{svg_path.stem}_rendered.png"
    rendered, renderer, renderer_warning = _render_svg(svg_path, rendered_png, source_rgba.shape[1], source_rgba.shape[0])
    rendered_rgba = rendered if rendered is not None else source_rgba.copy()
    metric_values = _metrics(source_rgba, rendered_rgba)
    stats = _svg_stats(svg_path)
    diff_png = output_dir / f"{svg_path.stem}_diff.png"
    _diff_contact_sheet(source_rgba, rendered_rgba, diff_png)

    if renderer == "prepared-png-proxy":
        status = "degraded"
        recommended = "Use structural metrics and visual artifact review, or enable a native SVG renderer for exact raster metrics."
        primary_issues = [renderer_warning]
    elif metric_values["alpha_iou"] >= 0.96 and metric_values["rgba_ssim"] >= 0.9:
        status = "pass"
        recommended = "Candidate is suitable as a clean vector trace."
        primary_issues = []
    elif metric_values["alpha_iou"] >= 0.9:
        status = "warn"
        recommended = "Candidate is usable but should be reviewed or repaired."
        primary_issues = []
    else:
        status = "fail"
        recommended = "Run repair_svg_trace or use pixel mode for high fidelity."
        primary_issues = ["Low raster similarity between prepared source and SVG render."]

    report = {
        "status": status,
        "renderer": renderer,
        "renderer_warning": renderer_warning,
        "recommended_action": recommended,
        "source_image": str(source_image_path),
        "svg": str(svg_path),
        "prepared_png": str(prepared_png),
        "rendered_png": str(rendered_png) if rendered is not None else None,
        "diff_png": str(diff_png),
        "metrics": metric_values,
        "structure": stats,
        "primary_issues": primary_issues,
    }
    metrics_json = output_dir / f"{svg_path.stem}_assessment.json"
    metrics_json.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    report["metrics_json"] = str(metrics_json)
    return report


def run_svg_pipeline(
    input_path: str | Path,
    output_dir: str | Path,
    mode: str = "vtracer",
    mask_mode: str = "auto",
    quality_profile: str = "balanced",
    repair: bool = False,
) -> dict[str, Any]:
    output_dir = _path(output_dir)
    if mode == "both":
        candidates = [
            convert_image_to_svg(input_path, output_dir / "vtracer", "vtracer", mask_mode, quality_profile),
            convert_image_to_svg(input_path, output_dir / "pixel", "pixel", mask_mode, "fidelity"),
        ]
        result = {"mode": "both", "candidates": candidates}
    else:
        converted = convert_image_to_svg(input_path, output_dir, mode, mask_mode, quality_profile)
        validation = validate_svg_trace(input_path, converted["svg"], output_dir / "validation", converted["prepared_png"])
        result = {**converted, "validation": validation}

    manifest = output_dir / "pipeline_manifest.json"
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    result["manifest"] = str(manifest)
    if repair and mode != "both":
        result["repair"] = repair_svg_trace(manifest, output_dir / "repair")
    return result


def repair_svg_trace(manifest_path: str | Path, output_dir: str | Path, budget: int = 6) -> dict[str, Any]:
    manifest_path = _path(manifest_path)
    output_dir = _path(output_dir)
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    input_path = data["input"]
    mask_mode = data.get("mask_mode", "auto")
    profiles = ["compact", "balanced", "fidelity"][: max(1, min(3, budget))]
    candidates = []
    for profile in profiles:
        candidate = convert_image_to_svg(input_path, output_dir / profile, "vtracer", mask_mode, profile)
        validation = validate_svg_trace(input_path, candidate["svg"], output_dir / profile / "validation", candidate["prepared_png"])
        score = validation["metrics"]["alpha_iou"] + validation["metrics"]["rgba_ssim"] - min(candidate["path_count"], 1000) / 10000
        candidates.append({**candidate, "validation": validation, "score": score})
    best = max(candidates, key=lambda item: item["score"])
    result = {"strategy": "parameter-rerun", "best_candidate": best, "candidates": candidates}
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "repair_report.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return result
