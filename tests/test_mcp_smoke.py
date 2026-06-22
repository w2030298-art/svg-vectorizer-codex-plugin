import json
import os
import queue
import shutil
import site
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
import sys


SERVER = Path(__file__).resolve().parents[1] / "plugins" / "svg-vectorizer" / "server" / "mcp-server.cjs"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
NODE = shutil.which("node") or "node"


class JsonRpcServer:
    def __init__(self, env: dict[str, str] | None = None):
        self.proc = subprocess.Popen(
            [NODE, str(SERVER)],
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
        return self.request_raw(json.dumps(payload), timeout=timeout)

    def request_raw(self, line: str, timeout: float = 30.0) -> dict:
        assert self.proc.stdin is not None
        self.proc.stdin.write(line + "\n")
        self.proc.stdin.flush()
        try:
            response = self.lines.get(timeout=timeout)
        except queue.Empty as exc:
            raise AssertionError("server did not produce a response") from exc
        return json.loads(response)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.communicate(timeout=5)


def response_content(response: dict) -> dict:
    text = response["result"]["content"][0]["text"]
    return json.loads(text)


def fake_npm_bin(root: Path) -> Path:
    bin_dir = root / "fake-bin"
    bin_dir.mkdir()
    if os.name == "nt":
        npm = bin_dir / "npm.cmd"
        npm.write_text("@echo off\r\necho fake npm renderer unavailable 1>&2\r\nexit /b 1\r\n", encoding="utf-8")
    else:
        npm = bin_dir / "npm"
        npm.write_text("#!/bin/sh\necho fake npm renderer unavailable >&2\nexit 1\n", encoding="utf-8")
        npm.chmod(0o755)
    return bin_dir


def mcp_venv_python(home: Path) -> Path:
    venv = home / ".cache" / "svg-vectorizer-codex-plugin" / "venv"
    if os.name == "nt":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def isolated_mcp_env(home: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    env["LOCALAPPDATA"] = str(home / "AppData" / "Local")
    env["APPDATA"] = str(home / "AppData" / "Roaming")
    env["XDG_CACHE_HOME"] = str(home / ".cache")
    return env


def prepare_mcp_python_runtime(home: Path, env: dict[str, str]) -> None:
    python = mcp_venv_python(home)
    if not python.exists():
        subprocess.run(
            [sys.executable, "-m", "venv", str(python.parent.parent)],
            check=True,
            text=True,
            capture_output=True,
            timeout=120,
        )

    site_paths = [path for path in site.getsitepackages() if path]
    try:
        site_paths.append(site.getusersitepackages())
    except AttributeError:
        pass
    if env.get("PYTHONPATH"):
        site_paths.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(site_paths)


class McpSmokeTests(unittest.TestCase):
    def start_server(self, env: dict[str, str] | None = None) -> JsonRpcServer:
        server = JsonRpcServer(env)
        self.addCleanup(server.close)
        return server

    def test_tools_list_contains_pipeline_tools(self):
        server = self.start_server()

        response = server.request(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/list",
                "params": {},
            }
        )

        names = {tool["name"] for tool in response["result"]["tools"]}
        self.assertIn("convert_image_to_svg", names)
        self.assertIn("validate_svg_trace", names)
        self.assertIn("repair_svg_trace", names)
        self.assertIn("run_svg_pipeline", names)

    def test_tools_call_dispatches_to_python_tool(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            home.mkdir()
            env = isolated_mcp_env(home)
            prepare_mcp_python_runtime(home, env)
            server = self.start_server(env)
            try:
                response = server.request(
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/call",
                        "params": {
                            "name": "convert_image_to_svg",
                            "arguments": {
                                "input_path": str(FIXTURES / "transparent_icon.png"),
                                "output_dir": str(root / "convert"),
                                "mode": "pixel",
                                "mask_mode": "alpha",
                                "quality_profile": "fidelity",
                            },
                        },
                    },
                    timeout=180,
                )
            finally:
                server.close()

            self.assertFalse(response["result"]["isError"])
            result = response_content(response)
            self.assertEqual(result["mode"], "pixel")
            self.assertEqual(result["mask_mode"], "alpha")
            self.assertTrue(Path(result["svg"]).exists())
            self.assertTrue(Path(result["prepared_png"]).exists())

    def test_tools_call_unknown_tool_returns_error_content(self):
        server = self.start_server()

        response = server.request(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "missing_tool", "arguments": {}},
            }
        )

        self.assertEqual(response["id"], 3)
        self.assertTrue(response["result"]["isError"])
        self.assertIn("Unknown tool: missing_tool", response["result"]["content"][0]["text"])

    def test_json_rpc_error_shapes(self):
        server = self.start_server()

        method_error = server.request({"jsonrpc": "2.0", "id": 4, "method": "missing/method"})
        parse_error = server.request_raw("{not-json")

        self.assertEqual(method_error["jsonrpc"], "2.0")
        self.assertEqual(method_error["id"], 4)
        self.assertEqual(method_error["error"]["code"], -32601)
        self.assertIn("Method not found", method_error["error"]["message"])
        self.assertEqual(parse_error["jsonrpc"], "2.0")
        self.assertIsNone(parse_error["id"])
        self.assertEqual(parse_error["error"]["code"], -32603)
        self.assertTrue(parse_error["error"]["message"])

    def test_validate_tool_degrades_when_renderer_bootstrap_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_bin = fake_npm_bin(root)
            home = root / "home"
            home.mkdir()
            env = isolated_mcp_env(home)
            prepare_mcp_python_runtime(home, env)
            env["PIP_CACHE_DIR"] = str(root / "pip-cache")
            env["PATH"] = str(fake_bin) + os.pathsep + env.get("PATH", "")
            server = self.start_server(env)

            try:
                converted_response = server.request(
                    {
                        "jsonrpc": "2.0",
                        "id": 5,
                        "method": "tools/call",
                        "params": {
                            "name": "convert_image_to_svg",
                            "arguments": {
                                "input_path": str(FIXTURES / "transparent_icon.png"),
                                "output_dir": str(root / "convert"),
                                "mode": "pixel",
                                "mask_mode": "alpha",
                                "quality_profile": "fidelity",
                            },
                        },
                    },
                    timeout=240,
                )
                converted = response_content(converted_response)

                validate_response = server.request(
                    {
                        "jsonrpc": "2.0",
                        "id": 6,
                        "method": "tools/call",
                        "params": {
                            "name": "validate_svg_trace",
                            "arguments": {
                                "source_image_path": str(FIXTURES / "transparent_icon.png"),
                                "svg_path": converted["svg"],
                                "prepared_png_path": converted["prepared_png"],
                                "output_dir": str(root / "validate"),
                            },
                        },
                    },
                    timeout=120,
                )
            finally:
                server.close()

        self.assertFalse(validate_response["result"]["isError"])
        report = response_content(validate_response)
        self.assertEqual(report["status"], "degraded")
        self.assertEqual(report["renderer"], "prepared-png-proxy")
        self.assertIsNone(report["rendered_png"])
        self.assertTrue(
            "fake npm renderer unavailable" in report["renderer_warning"]
            or "npm install @resvg/resvg-js failed" in report["renderer_warning"]
        )


if __name__ == "__main__":
    unittest.main()
