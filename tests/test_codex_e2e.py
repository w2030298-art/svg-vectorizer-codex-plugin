"""End-to-end verification of the Codex plugin shell.

These tests exercise the Codex side of the dual-platform structure as a real
interaction: the plugin is launched through the Codex manifest -> .mcp.json
wiring with the configured plugin-root cwd, a real ``run_svg_pipeline``
JSON-RPC call is driven over stdio, and the produced SVG is compared
byte-for-byte against the shared Python core.
"""

import json
import queue
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

from tests.test_claude_code_e2e import run_core_pipeline
from tests.test_mcp_smoke import (
    NODE,
    isolated_mcp_env,
    mcp_venv_python,
    prepare_mcp_python_runtime,
    response_content,
)


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "svg-vectorizer"
CODEX_MANIFEST = PLUGIN / ".codex-plugin" / "plugin.json"
SERVER = PLUGIN / "server" / "mcp-server.cjs"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
VERIFICATION_DOC = ROOT / "docs" / "verification" / "codex-e2e.md"


def codex_launch_config() -> tuple[list[str], Path]:
    """Build the server launch command exactly as Codex would.

    Codex reads the plugin manifest, resolves its ``mcpServers`` file relative
    to the installed plugin directory, then starts the configured command from
    the server cwd in that MCP file.
    """

    manifest = json.loads(CODEX_MANIFEST.read_text(encoding="utf-8"))
    mcp_path = (CODEX_MANIFEST.parent.parent / manifest["mcpServers"]).resolve()
    mcp = json.loads(mcp_path.read_text(encoding="utf-8"))
    server = mcp["mcpServers"]["svgVectorizer"]
    command = NODE if server["command"] == "node" else server["command"]
    cwd = (mcp_path.parent / server.get("cwd", ".")).resolve()
    return [command, *server["args"]], cwd


class CodexJsonRpcServer:
    """Minimal JSON-RPC stdio client that launches an explicit command."""

    def __init__(self, command: list[str], cwd: Path, env: dict[str, str]):
        self.proc = subprocess.Popen(
            command,
            cwd=cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            env=env,
            bufsize=1,
        )
        self.lines: queue.Queue[str] = queue.Queue()
        self.reader = threading.Thread(target=self._read_stdout, daemon=True)
        self.reader.start()
        self._closed = False

    def _read_stdout(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            self.lines.put(line)

    def request(self, payload: dict, timeout: float = 30.0) -> dict:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(payload) + "\n")
        self.proc.stdin.flush()
        try:
            response = self.lines.get(timeout=timeout)
        except queue.Empty as exc:  # pragma: no cover - defensive
            raise AssertionError("server did not produce a response") from exc
        return json.loads(response)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.communicate(timeout=5)


class CodexManifestLaunchTests(unittest.TestCase):
    def test_resolved_launch_command_uses_plugin_root_cwd_and_shared_core(self):
        command, cwd = codex_launch_config()

        self.assertEqual(Path(command[0]).name.split(".")[0], "node")
        self.assertEqual(command[1:], ["./server/mcp-server.cjs"])
        self.assertEqual(cwd, PLUGIN.resolve())
        self.assertTrue((cwd / command[1]).resolve().samefile(SERVER))


class CodexEndToEndTests(unittest.TestCase):
    def _start(self, env: dict[str, str]) -> CodexJsonRpcServer:
        command, cwd = codex_launch_config()
        server = CodexJsonRpcServer(command, cwd, env)
        self.addCleanup(server.close)
        return server

    def test_pipeline_runs_through_codex_launch_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            home.mkdir()
            env = isolated_mcp_env(home)
            env["PIP_CACHE_DIR"] = str(root / "pip-cache")
            env["NPM_CONFIG_CACHE"] = str(root / "npm-cache")
            prepare_mcp_python_runtime(home, env)
            server = self._start(env)

            response = server.request(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "run_svg_pipeline",
                        "arguments": {
                            "input_path": str(FIXTURES / "warm_icon.png"),
                            "output_dir": str(root / "pipeline"),
                            "mode": "vtracer",
                            "mask_mode": "warm-icon",
                        },
                    },
                },
                timeout=240,
            )

            self.assertFalse(response["result"]["isError"], response["result"])
            result = response_content(response)
            self.assertEqual(result["mode"], "vtracer")
            self.assertEqual(result["mask_mode"], "warm-icon")
            self.assertTrue(Path(result["svg"]).exists())
            self.assertTrue(Path(result["prepared_png"]).exists())
            self.assertTrue(Path(result["manifest"]).exists())
            self.assertGreater(result["path_count"], 0)
            self.assertGreater(result["svg_bytes"], 0)
            self.assertIn(result["validation"]["status"], {"pass", "warn", "fail", "degraded"})
            self.assertIn("alpha_iou", result["validation"]["metrics"])

    def test_codex_output_matches_shared_core(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            home.mkdir()
            env = isolated_mcp_env(home)
            env["PIP_CACHE_DIR"] = str(root / "pip-cache")
            env["NPM_CONFIG_CACHE"] = str(root / "npm-cache")
            prepare_mcp_python_runtime(home, env)

            arguments = {
                "input_path": str(FIXTURES / "transparent_icon.png"),
                "mode": "pixel",
                "mask_mode": "alpha",
                "quality_profile": "fidelity",
            }

            server = self._start(env)
            shell_response = server.request(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "run_svg_pipeline",
                        "arguments": {**arguments, "output_dir": str(root / "shell")},
                    },
                },
                timeout=240,
            )
            self.assertFalse(shell_response["result"]["isError"], shell_response["result"])
            shell_result = response_content(shell_response)
            shell_svg = Path(shell_result["svg"]).read_bytes()

            core_result = run_core_pipeline(
                {**arguments, "output_dir": str(root / "core")},
                env,
                str(mcp_venv_python(home)),
            )
            core_svg = Path(core_result["svg"]).read_bytes()

            self.assertEqual(shell_svg, core_svg)
            self.assertEqual(shell_result["path_count"], core_result["path_count"])


class CodexVerificationDocTests(unittest.TestCase):
    def test_codex_verification_doc_records_reproducible_command(self):
        self.assertTrue(VERIFICATION_DOC.exists())
        text = VERIFICATION_DOC.read_text(encoding="utf-8")
        self.assertIn("python -m unittest tests.test_codex_e2e -v", text)
        self.assertIn("run_svg_pipeline", text)


if __name__ == "__main__":
    unittest.main()
