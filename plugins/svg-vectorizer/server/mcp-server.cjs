#!/usr/bin/env node
"use strict";

const fs = require("fs");
const os = require("os");
const path = require("path");
const readline = require("readline");
const childProcess = require("child_process");

const SERVER_DIR = __dirname;
const REQUIREMENTS = path.join(SERVER_DIR, "requirements.txt");
const PY_CLI = path.join(SERVER_DIR, "pipeline_cli.py");
const CACHE_DIR = path.join(os.homedir(), ".cache", "svg-vectorizer-codex-plugin");
const VENV_DIR = path.join(CACHE_DIR, "venv");
const NODE_RUNTIME_DIR = path.join(CACHE_DIR, "node-runtime");
const NODE_MODULES = path.join(NODE_RUNTIME_DIR, "node_modules");
const RESVG_PACKAGE = path.join(NODE_MODULES, "@resvg", "resvg-js");
const RENDER_HELPER = path.join(SERVER_DIR, "render_svg_with_resvg.cjs");
const IS_WIN = process.platform === "win32";
const VENV_PYTHON = path.join(VENV_DIR, IS_WIN ? "Scripts/python.exe" : "bin/python");
const PYTHON_OVERRIDE_ENV = "SVG_VECTORIZER_PYTHON";
const SUPPORTED_PYTHON_MIN = { major: 3, minor: 10 };
const SUPPORTED_PYTHON_MAX = { major: 3, minor: 12 };
const SUPPORTED_PYTHON_LABEL = "Python 3.10 through 3.12";

const tools = [
  {
    name: "convert_image_to_svg",
    description: "Convert a raster image to SVG. Defaults to vtracer; use pixel mode only for explicit fidelity requests.",
    inputSchema: {
      type: "object",
      properties: {
        input_path: { type: "string" },
        output_dir: { type: "string" },
        mode: { type: "string", enum: ["vtracer", "pixel"], default: "vtracer" },
        mask_mode: { type: "string", enum: ["auto", "alpha", "flood", "warm-icon", "none"], default: "auto" },
        quality_profile: { type: "string", enum: ["compact", "balanced", "fidelity"], default: "balanced" },
        name: { type: "string" }
      },
      required: ["input_path", "output_dir"]
    }
  },
  {
    name: "validate_svg_trace",
    description: "Validate a generated SVG trace with browserless structural and image metrics.",
    inputSchema: {
      type: "object",
      properties: {
        source_image_path: { type: "string" },
        svg_path: { type: "string" },
        prepared_png_path: { type: "string" },
        output_dir: { type: "string" }
      },
      required: ["source_image_path", "svg_path", "output_dir"]
    }
  },
  {
    name: "repair_svg_trace",
    description: "Repair a trace by rerunning bounded parameter profiles and selecting the best candidate.",
    inputSchema: {
      type: "object",
      properties: {
        manifest_path: { type: "string" },
        output_dir: { type: "string" },
        budget: { type: "integer", minimum: 1, maximum: 6, default: 6 }
      },
      required: ["manifest_path", "output_dir"]
    }
  },
  {
    name: "run_svg_pipeline",
    description: "Run convert, validate, and optionally repair in one call.",
    inputSchema: {
      type: "object",
      properties: {
        input_path: { type: "string" },
        output_dir: { type: "string" },
        mode: { type: "string", enum: ["vtracer", "pixel", "both"], default: "vtracer" },
        mask_mode: { type: "string", enum: ["auto", "alpha", "flood", "warm-icon", "none"], default: "auto" },
        quality_profile: { type: "string", enum: ["compact", "balanced", "fidelity"], default: "balanced" },
        repair: { type: "boolean", default: false }
      },
      required: ["input_path", "output_dir"]
    }
  }
];

function parsePythonVersion(output) {
  const match = String(output || "").match(/Python\s+(\d+)\.(\d+)(?:\.(\d+))?/i);
  if (!match) return null;
  return {
    major: Number(match[1]),
    minor: Number(match[2]),
    patch: Number(match[3] || 0)
  };
}

function pythonVersionLabel(version) {
  return `${version.major}.${version.minor}.${version.patch}`;
}

function isSupportedPythonVersion(version) {
  return (
    version.major === SUPPORTED_PYTHON_MIN.major &&
    version.major === SUPPORTED_PYTHON_MAX.major &&
    version.minor >= SUPPORTED_PYTHON_MIN.minor &&
    version.minor <= SUPPORTED_PYTHON_MAX.minor
  );
}

function candidateLabel(candidate) {
  return [candidate.command, ...candidate.argsPrefix].join(" ");
}

function unsupportedPythonError(candidate, version) {
  return new Error(
    `Unsupported Python ${pythonVersionLabel(version)} selected for svg-vectorizer via ${candidateLabel(candidate)}. ` +
      `Supported versions are ${SUPPORTED_PYTHON_LABEL}. ` +
      "scikit-image may not provide wheels for this Python version in a fresh Windows environment, so pip can fall back to a native build that requires C/C++ Build Tools. " +
      `Install Python 3.10, 3.11, or 3.12, or set ${PYTHON_OVERRIDE_ENV} to a supported interpreter. ` +
      `Codex users can point ${PYTHON_OVERRIDE_ENV} at the bundled Python 3.12.13 python.exe.`
  );
}

function inspectPythonCandidate(candidate) {
  const result = childProcess.spawnSync(candidate.command, [...candidate.argsPrefix, "--version"], { encoding: "utf-8" });
  if (result.status !== 0) {
    return { available: false, candidate, output: (result.stderr || result.stdout || "").trim() };
  }
  const version = parsePythonVersion(`${result.stdout || ""}\n${result.stderr || ""}`);
  if (!version) {
    return { available: false, candidate, output: "could not parse Python version" };
  }
  return { available: true, supported: isSupportedPythonVersion(version), candidate, version };
}

function pythonCandidates() {
  if (IS_WIN) {
    return [
      { command: "py", argsPrefix: ["-3.12"] },
      { command: "py", argsPrefix: ["-3.11"] },
      { command: "py", argsPrefix: ["-3.10"] },
      { command: "py", argsPrefix: ["-3"] },
      { command: "python", argsPrefix: [] }
    ];
  }
  return [
    { command: "python3.12", argsPrefix: [] },
    { command: "python3.11", argsPrefix: [] },
    { command: "python3.10", argsPrefix: [] },
    { command: "python3", argsPrefix: [] },
    { command: "python", argsPrefix: [] }
  ];
}

function findPython() {
  const override = (process.env[PYTHON_OVERRIDE_ENV] || "").trim();
  if (override) {
    const selected = inspectPythonCandidate({ command: override, argsPrefix: [] });
    if (!selected.available) {
      throw new Error(
        `${PYTHON_OVERRIDE_ENV} is set to ${override}, but that interpreter could not report a Python version. ` +
          `Set ${PYTHON_OVERRIDE_ENV} to a ${SUPPORTED_PYTHON_LABEL} executable.`
      );
    }
    if (!selected.supported) throw unsupportedPythonError(selected.candidate, selected.version);
    return selected.candidate;
  }

  const unsupported = [];
  for (const candidate of pythonCandidates()) {
    const selected = inspectPythonCandidate(candidate);
    if (!selected.available) continue;
    if (selected.supported) return selected.candidate;
    unsupported.push(selected);
  }

  if (unsupported.length > 0) {
    throw unsupportedPythonError(unsupported[0].candidate, unsupported[0].version);
  }
  throw new Error(`No supported Python runtime found. Install ${SUPPORTED_PYTHON_LABEL}, or set ${PYTHON_OVERRIDE_ENV} to a supported interpreter.`);
}

function ensurePythonRuntime() {
  if (fs.existsSync(VENV_PYTHON)) return VENV_PYTHON;
  fs.mkdirSync(CACHE_DIR, { recursive: true });
  const base = findPython();
  let result = childProcess.spawnSync(base.command, [...base.argsPrefix, "-m", "venv", VENV_DIR], { encoding: "utf-8" });
  if (result.status !== 0) throw new Error(`Failed to create Python venv with ${candidateLabel(base)}: ${result.stderr || result.stdout}`);
  result = childProcess.spawnSync(VENV_PYTHON, ["-m", "pip", "install", "-r", REQUIREMENTS], { encoding: "utf-8" });
  if (result.status !== 0) throw new Error(`Failed to install Python requirements with ${candidateLabel(base)}: ${result.stderr || result.stdout}`);
  return VENV_PYTHON;
}

function ensureNodeRenderer() {
  if (fs.existsSync(RESVG_PACKAGE)) {
    return { ok: true, nodeModules: NODE_MODULES };
  }
  fs.mkdirSync(NODE_RUNTIME_DIR, { recursive: true });
  const packageJson = path.join(NODE_RUNTIME_DIR, "package.json");
  if (!fs.existsSync(packageJson)) {
    fs.writeFileSync(packageJson, JSON.stringify({ private: true, dependencies: {} }, null, 2));
  }
  const npmArgs = ["install", "@resvg/resvg-js@2.6.2", "--include=optional", "--omit=dev", "--no-audit", "--no-fund"];
  const npmCommand = IS_WIN ? (process.env.ComSpec || process.env.COMSPEC || "cmd.exe") : "npm";
  const spawnArgs = IS_WIN ? ["/d", "/s", "/c", ["npm.cmd", ...npmArgs].join(" ")] : npmArgs;
  const result = childProcess.spawnSync(
    npmCommand,
    spawnArgs,
    { cwd: NODE_RUNTIME_DIR, encoding: "utf-8", timeout: 180000 }
  );
  if (result.status !== 0) {
    const output = (result.stderr || result.stdout || (result.error && result.error.message) || "").trim();
    return {
      ok: false,
      error:
        `npm install @resvg/resvg-js@2.6.2 --include=optional failed. ` +
        `${output || "Renderer setup could not install resvg-js."} ` +
        "Install again with npm install --include=optional so the native resvg binding is present."
    };
  }
  return { ok: true, nodeModules: NODE_MODULES };
}

function callPythonTool(name, args) {
  const python = ensurePythonRuntime();
  const renderer = name === "convert_image_to_svg" ? { ok: false } : ensureNodeRenderer();
  const env = { ...process.env };
  if (renderer.ok) {
    env.SVG_VECTORIZER_NODE_MODULES = renderer.nodeModules;
    env.SVG_VECTORIZER_RENDER_HELPER = RENDER_HELPER;
  } else if (renderer.error) {
    env.SVG_VECTORIZER_RENDER_SETUP_ERROR = renderer.error;
  }
  const result = childProcess.spawnSync(
    python,
    [PY_CLI, "--tool", name, "--input-json", JSON.stringify(args || {})],
    { encoding: "utf-8", maxBuffer: 64 * 1024 * 1024, env }
  );
  if (result.status !== 0) {
    throw new Error(result.stderr || result.stdout || `Python tool ${name} failed`);
  }
  return JSON.parse(result.stdout);
}

async function handle(request) {
  const { id, method, params } = request;
  if (method === "initialize") {
    return {
      jsonrpc: "2.0",
      id,
      result: {
        protocolVersion: "2024-11-05",
        capabilities: { tools: {} },
        serverInfo: { name: "svg-vectorizer", version: "0.1.0" }
      }
    };
  }
  if (method === "notifications/initialized") return null;
  if (method === "tools/list") {
    return { jsonrpc: "2.0", id, result: { tools } };
  }
  if (method === "tools/call") {
    try {
      const toolName = params && params.name;
      if (!tools.some((tool) => tool.name === toolName)) throw new Error(`Unknown tool: ${toolName}`);
      const result = callPythonTool(toolName, params.arguments || {});
      return {
        jsonrpc: "2.0",
        id,
        result: {
          content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
          isError: false
        }
      };
    } catch (error) {
      return {
        jsonrpc: "2.0",
        id,
        result: {
          content: [{ type: "text", text: String(error && error.message ? error.message : error) }],
          isError: true
        }
      };
    }
  }
  return { jsonrpc: "2.0", id, error: { code: -32601, message: `Method not found: ${method}` } };
}

const rl = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
rl.on("line", async (line) => {
  if (!line.trim()) return;
  try {
    const response = await handle(JSON.parse(line));
    if (response) process.stdout.write(JSON.stringify(response) + "\n");
  } catch (error) {
    const response = {
      jsonrpc: "2.0",
      id: null,
      error: { code: -32603, message: String(error && error.message ? error.message : error) }
    };
    process.stdout.write(JSON.stringify(response) + "\n");
  }
});
