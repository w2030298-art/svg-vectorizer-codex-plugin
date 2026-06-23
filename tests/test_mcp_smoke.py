import json
import os
import queue
import shutil
import site
import subprocess
import tempfile
import textwrap
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


def run_mcp_server_harness(
    spawn_sync_source: str,
    action_source: str,
    env: dict[str, str] | None = None,
    platform: str | None = None,
) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        script = f"""
const fs = require("fs");
const os = require("os");
const path = require("path");
const vm = require("vm");
const serverPath = {json.dumps(str(SERVER))};
const fakeHome = {json.dumps(tmp)};
const calls = [];
const fakeChildProcess = {{
  spawnSync: {spawn_sync_source}
}};
const sandbox = {{
  Buffer,
  clearTimeout,
  console,
  setTimeout,
  __dirname: path.dirname(serverPath),
  process: {{
    ...process,
    env: {{ ...process.env, ...{json.dumps(env or {})} }},
    platform: {json.dumps(platform) if platform else "process.platform"},
    stdout: {{ write() {{}} }}
  }},
  require(name) {{
    if (name === "child_process") return fakeChildProcess;
    if (name === "os") return {{ ...os, homedir: () => fakeHome }};
    if (name === "readline") return {{ createInterface: () => ({{ on() {{}} }}) }};
    return require(name);
  }}
}};
vm.runInNewContext(fs.readFileSync(serverPath, "utf-8"), sandbox, {{ filename: serverPath }});
const payload = (() => {{
{textwrap.indent(action_source, "  ")}
}})();
console.log(JSON.stringify({{ payload, calls }}));
"""
        result = subprocess.run([NODE, "-e", script], check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


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

    def test_tools_list_exposes_checkerboard_mask_mode(self):
        server = self.start_server()

        response = server.request(
            {
                "jsonrpc": "2.0",
                "id": 11,
                "method": "tools/list",
                "params": {},
            }
        )

        tools = {tool["name"]: tool for tool in response["result"]["tools"]}
        for tool_name in ("convert_image_to_svg", "run_svg_pipeline"):
            enum = tools[tool_name]["inputSchema"]["properties"]["mask_mode"]["enum"]
            self.assertIn("checkerboard", enum)

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

    def test_tools_call_rejects_unsupported_python_with_actionable_error(self):
        result = run_mcp_server_harness(
            """
function(command, args = [], options = {}) {
  calls.push({ command, args });
  if (args.includes("--version")) {
    return { status: 0, stdout: "Python 3.14.0\\n", stderr: "" };
  }
  return { status: 1, stdout: "", stderr: "Unknown compiler(s): [['icl'], ['cl']]\\n" };
}
""",
            """
try {
  sandbox.ensurePythonRuntime();
  return { ok: true };
} catch (error) {
  return { ok: false, message: error.message };
}
""",
        )

        self.assertFalse(result["payload"]["ok"])
        message = result["payload"]["message"]
        self.assertIn("Unsupported Python 3.14", message)
        self.assertIn("Python 3.10 through 3.12", message)
        self.assertIn(".cache", message)
        self.assertIn("svg-vectorizer-codex-plugin", message)
        self.assertIn("cv2", message)
        self.assertIn("scikit-image", message)
        self.assertIn("SVG_VECTORIZER_PYTHON", message)
        self.assertIn("delete", message)
        self.assertNotIn("Unknown compiler", message)

    def test_python_bootstrap_uses_configured_interpreter_override(self):
        override = r"C:\Codex\python-3.12.13\python.exe"
        result = run_mcp_server_harness(
            """
function(command, args = [], options = {}) {
  calls.push({ command, args });
  if (args.includes("--version")) {
    return { status: 0, stdout: "Python 3.12.13\\n", stderr: "" };
  }
  return { status: 0, stdout: "", stderr: "" };
}
""",
            """
try {
  const python = sandbox.ensurePythonRuntime();
  return { ok: true, python };
} catch (error) {
  return { ok: false, message: error.message };
}
""",
            env={"SVG_VECTORIZER_PYTHON": override},
        )

        self.assertTrue(result["payload"]["ok"], result["payload"])
        self.assertEqual(result["calls"][0]["command"], override)

    def test_python_bootstrap_repairs_existing_venv_with_missing_core_dependency(self):
        result = run_mcp_server_harness(
            """
function(command, args = [], options = {}) {
  calls.push({ command, args });
  const commandText = String(command).replace(/\\\\/g, "/");
  const isVenvPython = commandText.endsWith("/.cache/svg-vectorizer-codex-plugin/venv/Scripts/python.exe");
  if (isVenvPython && args.includes("-c")) {
    const probeNumber = calls.filter((call) => call.args.includes("-c")).length;
    if (probeNumber === 1) {
      return { status: 1, stdout: "", stderr: "ModuleNotFoundError: No module named 'cv2'\\n" };
    }
    return { status: 0, stdout: "", stderr: "" };
  }
  if (isVenvPython && args.join(" ").includes("-m pip install")) {
    return { status: 0, stdout: "installed\\n", stderr: "" };
  }
  return { status: 1, stdout: "", stderr: `unexpected ${command} ${args.join(" ")}` };
}
""",
            """
const venvPython = path.join(fakeHome, ".cache", "svg-vectorizer-codex-plugin", "venv", "Scripts", "python.exe");
fs.mkdirSync(path.dirname(venvPython), { recursive: true });
fs.writeFileSync(venvPython, "", "utf-8");
try {
  const python = sandbox.ensurePythonRuntime();
  return {
    ok: true,
    python,
    venvExists: fs.existsSync(path.dirname(path.dirname(venvPython)))
  };
} catch (error) {
  return { ok: false, message: error.message };
}
""",
            platform="win32",
        )

        self.assertTrue(result["payload"]["ok"], result["payload"])
        joined_calls = [" ".join([call["command"], *call["args"]]) for call in result["calls"]]
        self.assertTrue(any("import cv2" in call for call in joined_calls))
        self.assertTrue(any("-m pip install -r" in call for call in joined_calls))
        self.assertEqual(len([call for call in joined_calls if "import cv2" in call]), 2)

    def test_python_bootstrap_cleans_bad_venv_after_pip_install_failure(self):
        result = run_mcp_server_harness(
            """
function(command, args = [], options = {}) {
  calls.push({ command, args });
  const commandText = String(command).replace(/\\\\/g, "/");
  const isVenvPython = commandText.endsWith("/.cache/svg-vectorizer-codex-plugin/venv/Scripts/python.exe");
  if (isVenvPython && args.includes("-c")) {
    return { status: 1, stdout: "", stderr: "ModuleNotFoundError: No module named 'cv2'\\n" };
  }
  if (isVenvPython && args.join(" ").includes("-m pip install")) {
    return { status: 1, stdout: "", stderr: "Unknown compiler(s): [['cl']]\\n" };
  }
  if (args.includes("--version")) {
    return { status: 0, stdout: "Python 3.12.13\\n", stderr: "" };
  }
  if (args.join(" ").includes("-m venv")) {
    const venvPython = path.join(fakeHome, ".cache", "svg-vectorizer-codex-plugin", "venv", "Scripts", "python.exe");
    fs.mkdirSync(path.dirname(venvPython), { recursive: true });
    fs.writeFileSync(venvPython, "", "utf-8");
    return { status: 0, stdout: "", stderr: "" };
  }
  return { status: 1, stdout: "", stderr: `unexpected ${command} ${args.join(" ")}` };
}
""",
            """
const venvPython = path.join(fakeHome, ".cache", "svg-vectorizer-codex-plugin", "venv", "Scripts", "python.exe");
fs.mkdirSync(path.dirname(venvPython), { recursive: true });
fs.writeFileSync(venvPython, "", "utf-8");
try {
  sandbox.ensurePythonRuntime();
  return { ok: true, venvExists: fs.existsSync(path.dirname(path.dirname(venvPython))) };
} catch (error) {
  return {
    ok: false,
    message: error.message,
    venvExists: fs.existsSync(path.dirname(path.dirname(venvPython)))
  };
}
""",
            platform="win32",
        )

        self.assertFalse(result["payload"]["ok"])
        self.assertFalse(result["payload"]["venvExists"])
        message = result["payload"]["message"]
        self.assertIn("venv", message)
        self.assertIn("cv2", message)
        self.assertIn("Python 3.12.13", message)
        self.assertIn("SVG_VECTORIZER_PYTHON", message)
        self.assertNotEqual(message.strip(), "ModuleNotFoundError: No module named 'cv2'")

    def test_python_bootstrap_auto_discovers_codex_bundled_python(self):
        result = run_mcp_server_harness(
            """
function(command, args = [], options = {}) {
  calls.push({ command, args });
  const commandText = String(command).replace(/\\\\/g, "/");
  const isBundledPython = commandText.endsWith("/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe");
  const isVenvPython = commandText.endsWith("/.cache/svg-vectorizer-codex-plugin/venv/Scripts/python.exe");
  if (args.includes("--version")) {
    if (isBundledPython) return { status: 0, stdout: "Python 3.12.13\\n", stderr: "" };
    if (String(command) === "py" && ["-3.12", "-3.11", "-3.10"].some((flag) => args.includes(flag))) {
      return { status: 1, stdout: "", stderr: "No suitable Python runtime found\\n" };
    }
    return { status: 0, stdout: "Python 3.14.0\\n", stderr: "" };
  }
  if (isBundledPython && args.join(" ").includes("-m venv")) {
    const venvPython = path.join(fakeHome, ".cache", "svg-vectorizer-codex-plugin", "venv", "Scripts", "python.exe");
    fs.mkdirSync(path.dirname(venvPython), { recursive: true });
    fs.writeFileSync(venvPython, "", "utf-8");
    return { status: 0, stdout: "", stderr: "" };
  }
  if (isVenvPython && args.join(" ").includes("-m pip install")) {
    return { status: 0, stdout: "installed\\n", stderr: "" };
  }
  if (isVenvPython && args.includes("-c")) {
    return { status: 0, stdout: "", stderr: "" };
  }
  return { status: 1, stdout: "", stderr: `unexpected ${command} ${args.join(" ")}` };
}
""",
            """
const bundled = path.join(fakeHome, ".cache", "codex-runtimes", "codex-primary-runtime", "dependencies", "python", "python.exe");
fs.mkdirSync(path.dirname(bundled), { recursive: true });
fs.writeFileSync(bundled, "", "utf-8");
try {
  const python = sandbox.ensurePythonRuntime();
  return { ok: true, python };
} catch (error) {
  return { ok: false, message: error.message };
}
""",
            platform="win32",
        )

        self.assertTrue(result["payload"]["ok"], result["payload"])
        venv_calls = [call for call in result["calls"] if "-m" in call["args"] and "venv" in call["args"]]
        self.assertEqual(len(venv_calls), 1)
        self.assertIn("codex-runtimes", venv_calls[0]["command"].replace("\\", "/"))

    def test_validate_bootstrap_installs_resvg_with_optional_dependencies(self):
        result = run_mcp_server_harness(
            """
function(command, args = [], options = {}) {
  calls.push({ command, args });
  return { status: 1, stdout: "", stderr: "fake npm renderer unavailable\\n" };
}
""",
            """
return { renderer: sandbox.ensureNodeRenderer() };
""",
        )

        self.assertFalse(result["payload"]["renderer"]["ok"])
        npm_args = " ".join(result["calls"][0]["args"])
        self.assertIn("@resvg/resvg-js@2.6.2", npm_args)
        self.assertIn("--include=optional", npm_args)

    def test_validate_bootstrap_invokes_windows_npm_cmd_through_shell(self):
        result = run_mcp_server_harness(
            """
function(command, args = [], options = {}) {
  calls.push({ command, args });
  return { status: 1, stdout: "", stderr: "fake npm renderer unavailable\\n" };
}
""",
            """
return { renderer: sandbox.ensureNodeRenderer() };
""",
            env={"ComSpec": r"C:\Windows\System32\cmd.exe"},
            platform="win32",
        )

        call = result["calls"][0]
        self.assertEqual(call["command"], r"C:\Windows\System32\cmd.exe")
        self.assertEqual(call["args"][:3], ["/d", "/s", "/c"])
        self.assertIn("npm.cmd install @resvg/resvg-js@2.6.2 --include=optional", call["args"][3])

    def test_validate_bootstrap_reports_npm_spawn_error(self):
        result = run_mcp_server_harness(
            """
function(command, args = [], options = {}) {
  calls.push({ command, args });
  return { status: null, stdout: "", stderr: "", error: { message: "spawn npm.cmd ENOENT" } };
}
""",
            """
return { renderer: sandbox.ensureNodeRenderer() };
""",
        )

        error = result["payload"]["renderer"]["error"]
        self.assertIn("npm install @resvg/resvg-js@2.6.2 --include=optional failed", error)
        self.assertIn("spawn npm.cmd ENOENT", error)

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
        self.assertIn("npm install @resvg/resvg-js@2.6.2 --include=optional failed", report["renderer_warning"])

    def test_python_tool_timeout_is_reported_with_actionable_error(self):
        result = run_mcp_server_harness(
            """
function(command, args = [], options = {}) {
  calls.push({ command, args, timeout: options.timeout || null });
  const commandText = String(command).replace(/\\\\/g, "/");
  const isVenvPython = commandText.endsWith("/.cache/svg-vectorizer-codex-plugin/venv/Scripts/python.exe");
  if (isVenvPython && args.includes("-c")) {
    return { status: 0, stdout: '{"missing": []}\\n', stderr: "" };
  }
  if (isVenvPython && args.includes("--tool")) {
    return {
      status: null,
      stdout: "",
      stderr: "",
      error: { code: "ETIMEDOUT", message: "spawnSync python ETIMEDOUT" }
    };
  }
  return { status: 1, stdout: "", stderr: `unexpected ${command} ${args.join(" ")}` };
}
""",
            """
const venvPython = path.join(fakeHome, ".cache", "svg-vectorizer-codex-plugin", "venv", "Scripts", "python.exe");
fs.mkdirSync(path.dirname(venvPython), { recursive: true });
fs.writeFileSync(venvPython, "", "utf-8");
try {
  sandbox.callPythonTool("convert_image_to_svg", { input_path: "input.png", output_dir: "out" });
  return { ok: true };
} catch (error) {
  return { ok: false, message: error.message };
}
""",
            platform="win32",
        )

        self.assertFalse(result["payload"]["ok"])
        self.assertIn("Python tool convert_image_to_svg timed out after 120 seconds", result["payload"]["message"])
        tool_calls = [call for call in result["calls"] if "--tool" in call["args"]]
        self.assertEqual(tool_calls[0]["timeout"], 120000)

if __name__ == "__main__":
    unittest.main()
