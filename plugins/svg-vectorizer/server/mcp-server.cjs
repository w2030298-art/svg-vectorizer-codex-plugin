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

function findPython() {
  const candidates = IS_WIN ? ["py", "python"] : ["python3", "python"];
  for (const command of candidates) {
    const result = childProcess.spawnSync(command, IS_WIN && command === "py" ? ["--version"] : ["--version"], { encoding: "utf-8" });
    if (result.status === 0) return { command, argsPrefix: IS_WIN && command === "py" ? ["-3"] : [] };
  }
  throw new Error("No Python runtime found. Install Python 3.11+ and retry.");
}

function ensurePythonRuntime() {
  if (fs.existsSync(VENV_PYTHON)) return VENV_PYTHON;
  fs.mkdirSync(CACHE_DIR, { recursive: true });
  const base = findPython();
  let result = childProcess.spawnSync(base.command, [...base.argsPrefix, "-m", "venv", VENV_DIR], { encoding: "utf-8" });
  if (result.status !== 0) throw new Error(`Failed to create Python venv: ${result.stderr || result.stdout}`);
  result = childProcess.spawnSync(VENV_PYTHON, ["-m", "pip", "install", "-r", REQUIREMENTS], { encoding: "utf-8" });
  if (result.status !== 0) throw new Error(`Failed to install Python requirements: ${result.stderr || result.stdout}`);
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
  const npmCommand = IS_WIN ? "npm.cmd" : "npm";
  const result = childProcess.spawnSync(
    npmCommand,
    ["install", "@resvg/resvg-js@2.6.2", "--omit=dev", "--no-audit", "--no-fund"],
    { cwd: NODE_RUNTIME_DIR, encoding: "utf-8", timeout: 180000 }
  );
  if (result.status !== 0) {
    return { ok: false, error: result.stderr || result.stdout || "npm install @resvg/resvg-js failed" };
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
