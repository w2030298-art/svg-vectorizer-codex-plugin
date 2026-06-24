"""End-to-end verification of the Claude Code plugin shell.

These tests exercise the Claude Code side of the dual-platform structure as a
real interaction: the plugin is launched exactly as Claude Code launches it
(resolving the ``${CLAUDE_PLUGIN_ROOT}`` placeholder from
``.claude-plugin/plugin.json``), a real ``run_svg_pipeline`` JSON-RPC call is
driven over stdio, and the produced SVG is compared byte-for-byte against the
shared Python core.

The single core (``server/mcp-server.cjs`` -> ``pipeline_cli.py``) is shared by
both the Codex and Claude Code shells, and the manifest invariants in
``tests.test_plugin_manifests`` already lock that both shells target the same
script. This module adds the missing piece: proof that the Claude Code launch
path actually runs the pipeline and that its output matches the core, so the
same input produces a consistent SVG on the Claude Code platform.
"""

import json
import queue
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

from tests.test_mcp_smoke import (
    NODE,
    isolated_mcp_env,
    mcp_venv_python,
    prepare_mcp_python_runtime,
    response_content,
)


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "svg-vectorizer"
CLAUDE_MANIFEST = PLUGIN / ".claude-plugin" / "plugin.json"
SERVER = PLUGIN / "server" / "mcp-server.cjs"
PY_CLI = PLUGIN / "server" / "pipeline_cli.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def claude_code_launch_command() -> list[str]:
    """Build the server launch command exactly as Claude Code would.

    Claude Code reads the inline ``mcpServers`` block from the plugin manifest
    and substitutes ``${CLAUDE_PLUGIN_ROOT}`` with the installed plugin
    directory before spawning the process.
    """

    manifest = json.loads(CLAUDE_MANIFEST.read_text(encoding="utf-8"))
    server = manifest["mcpServers"]["svgVectorizer"]
    command = NODE if server["command"] == "node" else server["command"]
    args = [arg.replace("${CLAUDE_PLUGIN_ROOT}", str(PLUGIN)) for arg in server["args"]]
    return [command, *args]


class ClaudeCodeJsonRpcServer:
    """Minimal JSON-RPC stdio client that launches an explicit command."""

    def __init__(self, command: list[str], env: dict[str, str]):
        self.proc = subprocess.Popen(
            command,
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


def run_core_pipeline(arguments: dict, env: dict[str, str], python: str) -> dict:
    """Run run_svg_pipeline straight through the shared Python core."""

    result = subprocess.run(
        [
            python,
            str(PY_CLI),
            "--tool",
            "run_svg_pipeline",
            "--input-json",
            json.dumps(arguments),
        ],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=180,
    )
    if result.returncode != 0:
        raise AssertionError(f"core pipeline failed: {result.stderr or result.stdout}")
    return json.loads(result.stdout)


class ClaudeCodeManifestLaunchTests(unittest.TestCase):
    def test_resolved_launch_command_points_at_shared_core(self):
        command = claude_code_launch_command()

        # node <pluginRoot>/server/mcp-server.cjs, with the placeholder resolved
        # to the real shared core that Codex also uses.
        self.assertEqual(Path(command[0]).name.split(".")[0], "node")
        self.assertEqual(len(command), 2)
        resolved = Path(command[1])
        self.assertTrue(resolved.is_absolute())
        self.assertTrue(resolved.exists())
        self.assertTrue(resolved.samefile(SERVER))
        self.assertNotIn("${CLAUDE_PLUGIN_ROOT}", command[1])


class ClaudeCodeEndToEndTests(unittest.TestCase):
    def _start(self, env: dict[str, str]) -> ClaudeCodeJsonRpcServer:
        server = ClaudeCodeJsonRpcServer(claude_code_launch_command(), env)
        self.addCleanup(server.close)
        return server

    def test_pipeline_runs_through_claude_code_launch_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            home.mkdir()
            env = isolated_mcp_env(home)
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

            # A real conversion produced real vector structure.
            self.assertGreater(result["path_count"], 0)
            self.assertGreater(result["svg_bytes"], 0)

            # Validation ran and reported a recognized status with metrics.
            validation = result["validation"]
            self.assertIn(validation["status"], {"pass", "warn", "fail", "degraded"})
            self.assertIn("alpha_iou", validation["metrics"])

    def test_claude_code_output_matches_shared_core(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            home.mkdir()
            env = isolated_mcp_env(home)
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

            # Same input, same args, straight through the shared Python core,
            # using the same runtime venv the shell bootstrapped.
            core_result = run_core_pipeline(
                {**arguments, "output_dir": str(root / "core")},
                env,
                str(mcp_venv_python(home)),
            )
            core_svg = Path(core_result["svg"]).read_bytes()

            # The Claude Code shell adds no divergence: byte-identical SVG, which
            # is what makes cross-platform output consistent for the same input.
            self.assertEqual(shell_svg, core_svg)
            self.assertEqual(shell_result["path_count"], core_result["path_count"])


if __name__ == "__main__":
    unittest.main()
