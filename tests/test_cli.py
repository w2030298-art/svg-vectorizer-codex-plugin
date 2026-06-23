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
            self.assertEqual(output_dir, Path(payload["svg"]).parent)

    def test_convert_subcommand_reports_runtime_errors_without_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_cli("convert", str(Path(tmp) / "missing.png"), str(Path(tmp) / "out"))

            self.assertEqual(result.returncode, 1)
            self.assertIn("error:", result.stderr)
            self.assertIn("missing.png", result.stderr)
            self.assertNotIn("Traceback", result.stderr)

    def test_pyproject_declares_svg_vectorizer_console_script(self):
        metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

        self.assertEqual(
            metadata["project"]["scripts"]["svg-vectorizer"],
            "pipeline_cli:main",
        )


if __name__ == "__main__":
    unittest.main()
