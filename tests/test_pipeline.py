import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

import sys

PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "plugins" / "svg-vectorizer"
sys.path.insert(0, str(PLUGIN_ROOT / "server"))

from svg_vectorizer_pipeline import (
    convert_image_to_svg,
    repair_svg_trace,
    run_svg_pipeline,
    validate_svg_trace,
)


def make_icon(path: Path) -> None:
    image = Image.new("RGB", (96, 96), (150, 125, 100))
    draw = ImageDraw.Draw(image)
    draw.ellipse((34, 8, 62, 36), fill=(255, 150, 30), outline=(8, 2, 0), width=4)
    draw.rounded_rectangle((25, 35, 72, 85), radius=10, fill=(255, 150, 30), outline=(8, 2, 0), width=4)
    draw.rounded_rectangle((12, 38, 30, 60), radius=4, fill=(255, 150, 30), outline=(8, 2, 0), width=4)
    image.save(path)


class PipelineTests(unittest.TestCase):
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
