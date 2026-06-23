import json
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "plugins" / "svg-vectorizer" / "server" / "pipeline_cli.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
VENV_PYTHON = ROOT / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
PYTHON = str(VENV_PYTHON if VENV_PYTHON.exists() else sys.executable)


class ConsoleCliTests(unittest.TestCase):
    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [PYTHON, str(CLI), *args],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=120,
        )

    def test_convert_subcommand_writes_svg(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "convert"

            result = self.run_cli(
                "convert",
                str(FIXTURES / "transparent_icon.png"),
                str(output_dir),
                "--mode",
                "pixel",
                "--mask-mode",
                "alpha",
                "--quality-profile",
                "fidelity",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["mode"], "pixel")
            self.assertEqual(payload["mask_mode"], "alpha")
            self.assertTrue(Path(payload["svg"]).exists())
            self.assertTrue(Path(payload["svg"]).parent.samefile(output_dir))

    def test_convert_subcommand_reports_runtime_errors_without_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_cli("convert", str(Path(tmp) / "missing.png"), str(Path(tmp) / "out"))

            self.assertEqual(result.returncode, 1)
            self.assertIn("error:", result.stderr)
            self.assertIn("missing.png", result.stderr)
            self.assertNotIn("Traceback", result.stderr)

    def test_batch_subcommand_writes_summary_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "batch"

            result = self.run_cli(
                "batch",
                str(FIXTURES),
                str(output_dir),
                "--mode",
                "pixel",
                "--mask-mode",
                "auto",
                "--quality-profile",
                "fidelity",
                "--max-workers",
                "2",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["total"], 3)
            self.assertEqual(payload["succeeded"], 3)
            self.assertEqual(payload["failed"], 0)
            self.assertTrue(Path(payload["manifest"]).exists())
            self.assertTrue((output_dir / "transparent_icon" / "transparent_icon_pixel.svg").exists())

    def test_pyproject_declares_svg_vectorizer_console_script(self):
        metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

        self.assertEqual(
            metadata["project"]["scripts"]["svg-vectorizer"],
            "pipeline_cli:main",
        )


if __name__ == "__main__":
    unittest.main()
