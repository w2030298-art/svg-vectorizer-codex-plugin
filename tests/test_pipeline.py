import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

import sys

PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "plugins" / "svg-vectorizer"
sys.path.insert(0, str(PLUGIN_ROOT / "server"))
FIXTURES = Path(__file__).resolve().parent / "fixtures"
RENDER_HELPER = PLUGIN_ROOT / "server" / "render_svg_with_resvg.cjs"

from svg_vectorizer_pipeline import (
    _render_svg,
    convert_image_to_svg,
    repair_svg_trace,
    run_batch_pipeline,
    run_svg_pipeline,
    validate_svg_trace,
)


def fixture(name: str) -> Path:
    return FIXTURES / name


def ensure_resvg_node_modules() -> Path:
    runtime = Path(tempfile.gettempdir()) / "svg-vectorizer-test-resvg-runtime"
    package = runtime / "node_modules" / "@resvg" / "resvg-js"
    if package.exists():
        return runtime / "node_modules"

    npm = shutil.which("npm")
    if npm is None:
        raise RuntimeError("npm is required for renderer-enabled validation tests")

    runtime.mkdir(parents=True, exist_ok=True)
    (runtime / "package.json").write_text('{"private": true, "dependencies": {}}\n', encoding="utf-8")
    subprocess.run(
        [npm, "install", "@resvg/resvg-js@2.6.2", "--omit=dev", "--no-audit", "--no-fund"],
        cwd=runtime,
        check=True,
        text=True,
        capture_output=True,
        timeout=180,
    )
    return runtime / "node_modules"


class EnvPatch:
    def __init__(self, updates: dict[str, str | None]):
        self.updates = updates
        self.previous: dict[str, str | None] = {}

    def __enter__(self):
        for key, value in self.updates.items():
            self.previous[key] = os.environ.get(key)
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def __exit__(self, exc_type, exc, tb):
        for key, value in self.previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def make_icon(path: Path) -> None:
    image = Image.new("RGB", (96, 96), (150, 125, 100))
    draw = ImageDraw.Draw(image)
    draw.ellipse((34, 8, 62, 36), fill=(255, 150, 30), outline=(8, 2, 0), width=4)
    draw.rounded_rectangle((25, 35, 72, 85), radius=10, fill=(255, 150, 30), outline=(8, 2, 0), width=4)
    draw.rounded_rectangle((12, 38, 30, 60), radius=4, fill=(255, 150, 30), outline=(8, 2, 0), width=4)
    image.save(path)


class PipelineTests(unittest.TestCase):
    def test_conversion_accepts_png_jpeg_and_webp_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cases = [
                ("icon.png", None, "PNG"),
                ("icon.jpg", "JPEG", "JPEG"),
                ("icon.webp", "WEBP", "WEBP"),
            ]

            for filename, image_format, expected_format in cases:
                with self.subTest(filename=filename):
                    source = root / filename
                    image = Image.new("RGB", (32, 24), (240, 240, 240))
                    draw = ImageDraw.Draw(image)
                    draw.rectangle((6, 5, 25, 18), fill=(20, 100, 210))
                    image.save(source, format=image_format)

                    result = convert_image_to_svg(
                        input_path=source,
                        output_dir=root / f"out-{source.suffix[1:]}",
                        mode="pixel",
                        mask_mode="none",
                        quality_profile="fidelity",
                    )

                    self.assertEqual(result["input_format"], expected_format)
                    self.assertFalse(result["downsampled"])
                    self.assertEqual(result["width"], 32)
                    self.assertEqual(result["height"], 24)
                    self.assertTrue(Path(result["svg"]).exists())
                    self.assertGreater(result["path_count"], 0)

    def test_unsupported_image_format_has_clear_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "animated.gif"
            Image.new("RGB", (8, 8), (20, 30, 40)).save(source, format="GIF")

            with self.assertRaisesRegex(ValueError, "Unsupported input format.*GIF.*Supported formats: PNG, JPEG, WebP"):
                convert_image_to_svg(
                    input_path=source,
                    output_dir=root / "out",
                    mode="pixel",
                    mask_mode="none",
                    quality_profile="fidelity",
                )

    def test_large_image_is_downsampled_before_vectorization(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "wide.png"
            image = Image.new("RGB", (4096, 32), (255, 255, 255))
            ImageDraw.Draw(image).rectangle((0, 8, 4095, 23), fill=(0, 120, 220))
            image.save(source)

            result = convert_image_to_svg(
                input_path=source,
                output_dir=root / "out",
                mode="pixel",
                mask_mode="none",
                quality_profile="fidelity",
            )

            self.assertTrue(result["downsampled"])
            self.assertEqual(result["source_width"], 4096)
            self.assertEqual(result["source_height"], 32)
            self.assertLess(result["width"], result["source_width"])
            self.assertLessEqual(result["width"], result["max_input_side"])
            self.assertTrue(Path(result["prepared_png"]).exists())
            self.assertTrue(Path(result["svg"]).exists())

    def test_empty_transparent_image_writes_empty_pixel_svg(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "empty.png"
            Image.new("RGBA", (3, 3), (0, 0, 0, 0)).save(source)

            result = convert_image_to_svg(
                input_path=source,
                output_dir=root / "out",
                mode="pixel",
                mask_mode="alpha",
                quality_profile="fidelity",
            )

            self.assertEqual(result["foreground_pixels"], 0)
            self.assertEqual(result["path_count"], 0)
            self.assertTrue(Path(result["svg"]).exists())

    def test_single_color_and_tiny_inputs_convert(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for size in [(1, 1), (2, 3)]:
                with self.subTest(size=size):
                    source = root / f"solid-{size[0]}x{size[1]}.png"
                    Image.new("RGB", size, (10, 20, 30)).save(source)

                    result = convert_image_to_svg(
                        input_path=source,
                        output_dir=root / f"out-{size[0]}x{size[1]}",
                        mode="pixel",
                        mask_mode="none",
                        quality_profile="fidelity",
                    )

                    self.assertEqual(result["width"], size[0])
                    self.assertEqual(result["height"], size[1])
                    self.assertEqual(result["foreground_pixels"], size[0] * size[1])
                    self.assertGreater(result["path_count"], 0)

    def test_renderer_timeout_returns_readable_degraded_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            svg = root / "trace.svg"
            svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"/>', encoding="utf-8")

            with EnvPatch(
                {
                    "SVG_VECTORIZER_RENDER_HELPER": "slow-renderer.cjs",
                    "SVG_VECTORIZER_NODE_MODULES": str(root),
                    "SVG_VECTORIZER_RENDER_SETUP_ERROR": None,
                }
            ):
                with patch(
                    "svg_vectorizer_pipeline.subprocess.run",
                    side_effect=subprocess.TimeoutExpired(["node", "slow-renderer.cjs"], 60),
                ):
                    rendered, renderer, warning = _render_svg(svg, root / "rendered.png", 1, 1)

            self.assertIsNone(rendered)
            self.assertEqual(renderer, "prepared-png-proxy")
            self.assertIn("SVG renderer timed out after 60 seconds", warning)
            self.assertIn("slow-renderer.cjs", warning)

    def test_vtracer_conversion_writes_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "icon.png"
            make_icon(source)

            result = convert_image_to_svg(
                input_path=source,
                output_dir=root / "out",
                mode="vtracer",
                mask_mode="warm-icon",
                quality_profile="balanced",
            )

            self.assertEqual(result["mode"], "vtracer")
            self.assertTrue(Path(result["svg"]).exists())
            self.assertTrue(Path(result["prepared_png"]).exists())
            self.assertGreater(result["path_count"], 0)
            self.assertGreater(result["svg_bytes"], 100)

    def test_pixel_conversion_is_explicit_fidelity_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "icon.png"
            make_icon(source)

            result = convert_image_to_svg(
                input_path=source,
                output_dir=root / "out",
                mode="pixel",
                mask_mode="warm-icon",
                quality_profile="fidelity",
            )

            svg_text = Path(result["svg"]).read_text(encoding="utf-8")
            self.assertEqual(result["mode"], "pixel")
            self.assertIn("shape-rendering=\"crispEdges\"", svg_text)
            self.assertGreater(result["path_count"], 0)

    def test_validation_produces_llm_facing_assessment(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "icon.png"
            make_icon(source)
            converted = convert_image_to_svg(
                input_path=source,
                output_dir=root / "out",
                mode="vtracer",
                mask_mode="warm-icon",
                quality_profile="balanced",
            )

            report = validate_svg_trace(
                source_image_path=source,
                svg_path=Path(converted["svg"]),
                prepared_png_path=Path(converted["prepared_png"]),
                output_dir=root / "validate",
            )

            self.assertIn(report["status"], {"pass", "warn", "fail", "degraded"})
            self.assertIn("recommended_action", report)
            self.assertTrue(Path(report["metrics_json"]).exists())
            self.assertTrue(Path(report["diff_png"]).exists())

    def test_renderer_enabled_validation_uses_real_iou_and_ssim(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = fixture("transparent_icon.png")
            converted = convert_image_to_svg(
                input_path=source,
                output_dir=root / "convert",
                mode="pixel",
                mask_mode="alpha",
                quality_profile="fidelity",
            )

            with EnvPatch(
                {
                    "SVG_VECTORIZER_RENDER_HELPER": str(RENDER_HELPER),
                    "SVG_VECTORIZER_NODE_MODULES": str(ensure_resvg_node_modules()),
                    "SVG_VECTORIZER_RENDER_SETUP_ERROR": None,
                }
            ):
                report = validate_svg_trace(
                    source_image_path=source,
                    svg_path=Path(converted["svg"]),
                    prepared_png_path=Path(converted["prepared_png"]),
                    output_dir=root / "validate",
                )

            metrics = report["metrics"]
            self.assertEqual(report["renderer"], "resvg-js")
            self.assertIsNone(report["renderer_warning"])
            self.assertIn(report["status"], {"pass", "warn"})
            self.assertGreaterEqual(metrics["alpha_iou"], 0.96)
            self.assertGreaterEqual(metrics["rgba_ssim"], 0.9)
            self.assertGreater(metrics["source_foreground_pixels"], 0)
            self.assertGreater(metrics["rendered_foreground_pixels"], 0)
            self.assertTrue(Path(report["rendered_png"]).exists())
            metrics_payload = json.loads(Path(report["metrics_json"]).read_text(encoding="utf-8"))
            self.assertEqual(metrics_payload["renderer"], "resvg-js")

    def test_run_pipeline_both_mode_writes_vtracer_and_pixel_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = run_svg_pipeline(
                input_path=fixture("warm_icon.png"),
                output_dir=root / "both",
                mode="both",
                mask_mode="warm-icon",
                quality_profile="balanced",
                repair=False,
            )

            self.assertEqual(result["mode"], "both")
            self.assertEqual({candidate["mode"] for candidate in result["candidates"]}, {"vtracer", "pixel"})
            self.assertTrue(Path(result["manifest"]).exists())
            for candidate in result["candidates"]:
                self.assertTrue(Path(candidate["svg"]).exists())
                self.assertTrue(Path(candidate["prepared_png"]).exists())
                self.assertTrue(Path(candidate["manifest"]).exists())
                self.assertGreater(candidate["path_count"], 0)

    def test_batch_pipeline_records_successes_and_isolated_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inputs = root / "inputs"
            inputs.mkdir()
            shutil.copy2(fixture("transparent_icon.png"), inputs / "transparent_icon.png")
            shutil.copy2(fixture("warm_icon.png"), inputs / "warm_icon.png")
            (inputs / "broken.png").write_text("not an image\n", encoding="utf-8")

            result = run_batch_pipeline(
                input_path=inputs,
                output_dir=root / "batch",
                mode="pixel",
                mask_mode="auto",
                quality_profile="fidelity",
                max_workers=2,
            )

            self.assertEqual(result["total"], 3)
            self.assertEqual(result["succeeded"], 2)
            self.assertEqual(result["failed"], 1)
            self.assertTrue(Path(result["manifest"]).exists())

            successful = [item for item in result["items"] if item["status"] == "success"]
            failed = [item for item in result["items"] if item["status"] == "failed"]
            self.assertEqual(len(successful), 2)
            self.assertEqual(len(failed), 1)
            for item in successful:
                self.assertTrue(Path(item["svg"]).exists())
                self.assertTrue(Path(item["item_manifest"]).exists())
                self.assertIn("path_count", item["metrics"])
            self.assertTrue(failed[0]["input"].endswith("broken.png"))
            self.assertIn("error", failed[0])

    def test_batch_pipeline_accepts_glob_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inputs = root / "inputs"
            inputs.mkdir()
            shutil.copy2(fixture("transparent_icon.png"), inputs / "transparent_icon.png")
            shutil.copy2(fixture("warm_icon.png"), inputs / "warm_icon.png")

            result = run_batch_pipeline(
                input_path=str(inputs / "*.png"),
                output_dir=root / "batch",
                mode="pixel",
                mask_mode="auto",
                quality_profile="fidelity",
                max_workers=1,
            )

            self.assertEqual(result["total"], 2)
            self.assertEqual(result["succeeded"], 2)
            self.assertEqual(result["failed"], 0)
            self.assertTrue(Path(result["manifest"]).exists())

    def test_mask_modes_cover_alpha_flood_warm_icon_and_none(self):
        cases = [
            ("auto", "transparent_icon.png", "alpha", "partial"),
            ("alpha", "transparent_icon.png", "alpha", "partial"),
            ("flood", "flat_background.png", "flood", "partial"),
            ("warm-icon", "warm_icon.png", "warm-icon", "partial"),
            ("none", "flat_background.png", "none", "full"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for mask_mode, image_name, expected_mode, foreground_shape in cases:
                with self.subTest(mask_mode=mask_mode):
                    result = convert_image_to_svg(
                        input_path=fixture(image_name),
                        output_dir=root / mask_mode.replace("-", "_"),
                        mode="pixel",
                        mask_mode=mask_mode,
                        quality_profile="fidelity",
                    )
                    total_pixels = result["width"] * result["height"]
                    self.assertEqual(result["mask_mode"], expected_mode)
                    self.assertGreater(result["foreground_pixels"], 0)
                    if foreground_shape == "full":
                        self.assertEqual(result["foreground_pixels"], total_pixels)
                    else:
                        self.assertLess(result["foreground_pixels"], total_pixels)
                    self.assertTrue(Path(result["prepared_png"]).exists())
                    self.assertTrue(Path(result["svg"]).exists())

    def test_validation_degrades_when_renderer_is_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = fixture("transparent_icon.png")
            converted = convert_image_to_svg(
                input_path=source,
                output_dir=root / "convert",
                mode="pixel",
                mask_mode="alpha",
                quality_profile="fidelity",
            )

            with EnvPatch(
                {
                    "SVG_VECTORIZER_RENDER_HELPER": None,
                    "SVG_VECTORIZER_NODE_MODULES": None,
                    "SVG_VECTORIZER_RENDER_SETUP_ERROR": "unit-test renderer unavailable",
                }
            ):
                report = validate_svg_trace(
                    source_image_path=source,
                    svg_path=Path(converted["svg"]),
                    prepared_png_path=Path(converted["prepared_png"]),
                    output_dir=root / "validate",
                )

            self.assertEqual(report["status"], "degraded")
            self.assertEqual(report["renderer"], "prepared-png-proxy")
            self.assertEqual(report["renderer_warning"], "unit-test renderer unavailable")
            self.assertIsNone(report["rendered_png"])
            self.assertEqual(report["metrics"]["alpha_iou"], 1.0)
            self.assertEqual(report["metrics"]["rgba_ssim"], 1.0)

    def test_pipeline_tool_error_paths_are_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = fixture("flat_background.png")
            with self.assertRaisesRegex(ValueError, "mode must be 'vtracer' or 'pixel'"):
                convert_image_to_svg(source, root / "convert", mode="invalid")

            with self.assertRaisesRegex(ValueError, "mode must be 'vtracer' or 'pixel'"):
                run_svg_pipeline(source, root / "pipeline", mode="invalid")

            bad_manifest = root / "bad_manifest.json"
            bad_manifest.write_text("{}\n", encoding="utf-8")
            with self.assertRaises(KeyError):
                repair_svg_trace(bad_manifest, root / "repair")

    def test_repair_uses_parameter_reruns_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "icon.png"
            make_icon(source)
            first = run_svg_pipeline(
                input_path=source,
                output_dir=root / "first",
                mode="vtracer",
                mask_mode="warm-icon",
                repair=False,
            )

            repaired = repair_svg_trace(
                manifest_path=Path(first["manifest"]),
                output_dir=root / "repair",
                budget=2,
            )

            self.assertGreaterEqual(len(repaired["candidates"]), 1)
            self.assertEqual(repaired["strategy"], "parameter-rerun")
            self.assertTrue(Path(repaired["best_candidate"]["svg"]).exists())


if __name__ == "__main__":
    unittest.main()
