# SVG Vectorizer

[![CI](https://github.com/w2030298-art/svg-vectorizer-codex-plugin/actions/workflows/ci.yml/badge.svg)](https://github.com/w2030298-art/svg-vectorizer-codex-plugin/actions/workflows/ci.yml)

Image-to-SVG plugin for **Codex** and **Claude Code**. One core conversion
pipeline (`plugins/svg-vectorizer/server`) is shared by two thin platform
shells — `.codex-plugin` for Codex and `.claude-plugin` for Claude Code — so
both hosts expose the same convert, validate, and repair tools. It defaults to
compact editable `vtracer` output, can generate pixel-fidelity SVGs when exact
raster matching matters, and validates traces without starting browser or HTTP
preview services.

## Gallery

See the [real-world gallery](docs/gallery/real-world/README.md) for source
images, vtracer and pixel SVG candidates, diff contact sheets, assessment JSON,
and browser-open review screenshots across logo, icon, warm-icon, photo,
and baked RGB checkerboard inputs.

## Sources of truth

This README is the current-state snapshot and entry point, not a changelog.

- **Overview · how to run · architecture** -> this README.
- **Contributor architecture** -> [docs/architecture.md](docs/architecture.md).
- **Contributing workflow** -> [CONTRIBUTING.md](CONTRIBUTING.md).
- **Version / what shipped** -> [GitHub Releases](../../releases). The marketplace plugin is currently `v0.2.0`; the first GitHub Release is cut at `v0.2.0`.
- **Status · decisions · progress** -> [Linear project](https://linear.app/wentaoxu-personal-workplace/project/svg-vectorizer图像转-svg-插件加固功能扩展与双平台适配-668f68c543f2/overview).

Each fact has one home; this file links to the others and does not restate
version, status, or decision history.

## Install

### Codex

```powershell
codex plugin marketplace add w2030298-art/svg-vectorizer-codex-plugin --ref main
codex plugin add svg-vectorizer@svg-tools
```

Start a new Codex thread after installing the plugin so the MCP server and skill
instructions are loaded.

The first tool call creates runtime caches under
`~/.cache/svg-vectorizer-codex-plugin`:

- a Python venv with vtracer, OpenCV, scikit-image, Pillow, and NumPy
- a Node runtime folder with `@resvg/resvg-js` for browserless SVG rendering

The Codex launch path is verified end to end (same input -> consistent SVG) in
[docs/verification/codex-e2e.md](docs/verification/codex-e2e.md).

### Claude Code

The Claude Code shell reuses the same `server/` core as Codex through a thin
`.claude-plugin` manifest; the `server/` directory is never copied. Add this
repository as a plugin marketplace, then install the plugin:

```sh
claude plugin marketplace add w2030298-art/svg-vectorizer-codex-plugin@main
claude plugin install svg-vectorizer@svg-tools
```

Or interactively inside a Claude Code session:

```
/plugin marketplace add w2030298-art/svg-vectorizer-codex-plugin
/plugin install svg-vectorizer@svg-tools
```

Start a new Claude Code session after installing so the `svgVectorizer` MCP
server and skill are loaded. The first tool call builds the same runtime caches
under `~/.cache/svg-vectorizer-codex-plugin` as Codex: a Python venv with
vtracer, OpenCV, scikit-image, Pillow, and NumPy, plus a Node runtime folder
with `@resvg/resvg-js` for browserless SVG rendering.

The Claude Code launch path is verified end to end (same input → consistent
SVG) in [docs/verification/claude-code-e2e.md](docs/verification/claude-code-e2e.md).

### Standalone CLI

From a repository checkout, install the Python package in editable mode:

```sh
python -m pip install -e .
```

This exposes the `svg-vectorizer` command without going through MCP.

## Agent Routing

The bundled `svg-vectorizer` skill tells agents to route image-to-SVG requests
through the MCP tools in this order:

1. Use `run_svg_pipeline` by default with `mode: "vtracer"` and `mask_mode: "auto"`.
2. Use `mode: "pixel"` only for explicit pixel-level fidelity or exact-match requests.
3. Use `mode: "both"` when comparing vtracer and pixel routes.
4. Use `mask_mode: "checkerboard"` only for RGB/no-alpha inputs where transparency is baked as a light gray/white checkerboard. This is opt-in and is not selected by `auto`.
5. Run `repair_svg_trace` only when requested or when validation shows the trace needs another bounded parameter pass.

Background handling follows the same skill guidance: `alpha` for sources that
already have transparency, `warm-icon` for orange/black icons on gradient or
shadowed backgrounds, `flood` for mostly flat solid backgrounds, `checkerboard`
for baked light gray/white checkerboards in RGB inputs, and `none` only when the
full image including the background should be traced. `checkerboard` remains
opt-in so existing `auto` behavior does not change for prior inputs.

## Tool Reference

Tools are exposed through MCP in Codex and through the standalone
`svg-vectorizer` CLI for local development or scripts. The CLI subcommands map
to the core Python tools:

```sh
svg-vectorizer convert INPUT_IMAGE OUTPUT_DIR [--mode vtracer|pixel]
svg-vectorizer validate SOURCE_IMAGE SVG_PATH OUTPUT_DIR
svg-vectorizer repair MANIFEST_PATH OUTPUT_DIR [--budget 1..6]
svg-vectorizer pipeline INPUT_IMAGE OUTPUT_DIR [--mode vtracer|pixel|both] [--repair]
svg-vectorizer batch INPUT_DIR_OR_GLOB OUTPUT_DIR [--max-workers 2]
```

The legacy JSON shim used by the MCP server remains available for reproducible
tool payloads:

```sh
python3 plugins/svg-vectorizer/server/pipeline_cli.py --tool TOOL_NAME --input-json '{"key":"value"}'
```

### `run_svg_pipeline`

Runs conversion, validation, and optional repair in one call. This is the
default entry point for normal requests.

| Parameter | Required | Default | Notes |
| --- | --- | --- | --- |
| `input_path` | yes | - | Raster source path: PNG, JPEG/JPG, or WebP. Unsupported actual file formats are rejected with a readable error. |
| `output_dir` | yes | - | Directory for SVG, prepared PNG, validation artifacts, and `pipeline_manifest.json`. |
| `mode` | no | `vtracer` | `vtracer`, `pixel`, or `both`. `both` writes separate vtracer and pixel candidates. |
| `mask_mode` | no | `auto` | `auto`, `alpha`, `flood`, `warm-icon`, `checkerboard`, or `none`. |
| `quality_profile` | no | `balanced` | `compact`, `balanced`, or `fidelity`; applies to vtracer output. |
| `repair` | no | `false` | When `true`, runs bounded repair after a non-`both` pipeline. |

Output: a JSON result with the selected mode, artifact paths, SVG structure
stats, validation status and metrics for single-candidate modes, and
`pipeline_manifest.json`.

### `run_batch_pipeline`

Runs `run_svg_pipeline` for every supported image in a directory or glob. Batch
runs isolate per-image failures and always write a summary manifest when at
least one supported input is found.

| Parameter | Required | Default | Notes |
| --- | --- | --- | --- |
| `input_path` | yes | - | Directory, file, or glob pattern. Directory scans include direct child `png`, `jpg`, `jpeg`, and `webp` files. |
| `output_dir` | yes | - | Directory for per-image output folders plus `batch_manifest.json`. |
| `mode` | no | `vtracer` | `vtracer`, `pixel`, or `both`; passed through to `run_svg_pipeline`. |
| `mask_mode` | no | `auto` | `auto`, `alpha`, `flood`, `warm-icon`, `checkerboard`, or `none`. |
| `quality_profile` | no | `balanced` | `compact`, `balanced`, or `fidelity`. |
| `repair` | no | `false` | Runs bounded repair for each single-candidate image when true. |
| `max_workers` | no | `2` | Upper bound for concurrent image jobs. |

Output: `batch_manifest.json` with `total`, `succeeded`, `failed`, and one
item per input. Successful items include SVG paths, per-image manifests, and key
metrics. Failed items include `error_type` and `error` without stopping the
rest of the batch.

### `convert_image_to_svg`

Converts one raster image into one SVG candidate.

| Parameter | Required | Default | Notes |
| --- | --- | --- | --- |
| `input_path` | yes | - | Raster source path. |
| `output_dir` | yes | - | Directory for the prepared PNG, SVG, and candidate manifest. |
| `mode` | no | `vtracer` | `vtracer` or `pixel`. |
| `mask_mode` | no | `auto` | `auto`, `alpha`, `flood`, `warm-icon`, `checkerboard`, or `none`. |
| `quality_profile` | no | `balanced` | `compact`, `balanced`, or `fidelity`. Pixel mode ignores the vtracer settings but should use `fidelity` for clarity. |
| `name` | no | source stem | Optional output stem. |

Output: JSON with `svg`, `prepared_png`, candidate `manifest`, effective
`mask_mode`, detected `input_format`, source and prepared dimensions,
`downsampled` metadata, foreground pixel count, path/fill counts, and SVG
byte size.

### `validate_svg_trace`

Validates a generated SVG against its source image. It writes structural SVG
metrics, a prepared transparent source, a rendered image when the renderer is
available, a diff contact sheet, and an LLM-facing assessment JSON.

| Parameter | Required | Default | Notes |
| --- | --- | --- | --- |
| `source_image_path` | yes | - | Original raster source. |
| `svg_path` | yes | - | SVG candidate to validate. |
| `output_dir` | yes | - | Directory for validation artifacts. |
| `prepared_png_path` | no | generated from source | Reuse the prepared PNG from conversion when available. |

Output: JSON with `status` (`pass`, `warn`, `fail`, or `degraded`), renderer
name, renderer warning if degraded, `metrics`, SVG `structure`, `diff_png`, and
`metrics_json`.

### `repair_svg_trace`

Repairs by rerunning bounded vtracer parameter profiles and selecting the best
candidate. It does not manually edit SVG paths.

| Parameter | Required | Default | Notes |
| --- | --- | --- | --- |
| `manifest_path` | yes | - | Manifest from `run_svg_pipeline` or `convert_image_to_svg`. |
| `output_dir` | yes | - | Directory for repair candidates and `repair_report.json`. |
| `budget` | no | `6` | Integer from `1` to `6`; currently maps to up to three quality-profile reruns. |

Output: JSON with `strategy: "parameter-rerun"`, the best candidate, all
candidates, their validation reports, and `repair_report.json`.

## Examples

Run commands from the repository root after installing Python dependencies. The
fixtures are intentionally small so these commands are safe to copy into a local
checkout.

Convert and validate with the default vtracer route:

```sh
svg-vectorizer pipeline tests/fixtures/warm_icon.png tmp/readme-vtracer --mode vtracer --mask-mode warm-icon --quality-profile balanced
```

Create vtracer and pixel candidates for comparison:

```sh
svg-vectorizer pipeline tests/fixtures/transparent_icon.png tmp/readme-both --mode both --mask-mode alpha --quality-profile balanced
```

Validate an existing SVG candidate:

```sh
svg-vectorizer validate tests/fixtures/warm_icon.png tmp/readme-vtracer/warm_icon_vtracer.svg tmp/readme-vtracer/validation-again --prepared-png-path tmp/readme-vtracer/warm_icon_prepared.png
```

Remove a baked RGB checkerboard background explicitly:

```sh
svg-vectorizer pipeline docs/gallery/real-world/sources/checkerboard_rgb_icon.png tmp/readme-checkerboard --mode vtracer --mask-mode checkerboard --quality-profile balanced
```

Run bounded repair against a previous pipeline manifest:

```sh
svg-vectorizer repair tmp/readme-vtracer/pipeline_manifest.json tmp/readme-repair --budget 2
```

Run every fixture image with at most two workers:

```sh
svg-vectorizer batch tests/fixtures tmp/readme-batch --mode pixel --mask-mode auto --quality-profile fidelity --max-workers 2
```

## Degraded Mode And Troubleshooting

### Renderer unavailable

Validation uses `@resvg/resvg-js` when the Node runtime is available. If renderer
setup fails, exits non-zero, or times out, validation returns
`status: "degraded"` and `renderer: "prepared-png-proxy"` instead of failing
the whole request. In degraded mode, use structural metrics plus the generated
artifacts for review; exact SVG raster metrics are unavailable until the
renderer is installed.

### Baked checkerboard backgrounds

RGB images sometimes contain fake transparency: the transparent area has already
been baked into pixels as an alternating light gray/white checkerboard. Use
`mask_mode: "checkerboard"` for those cases. The detector looks for two light
border colors arranged as regular alternating tiles, then makes matching
checkerboard pixels transparent before vectorization. The default `auto` mode
still chooses alpha when real transparency exists and otherwise uses flood, so
existing alpha/flood/warm-icon/none workflows keep their previous behavior.

### Input formats and size limits

Conversion accepts PNG, JPEG/JPG, and WebP inputs based on the actual decoded
file format. Valid files in other formats, such as GIF, and corrupt image files
return a readable error that names the supported formats.

Before masking or vectorization, inputs larger than `1,048,576` pixels or with a
side longer than `2048` pixels are downsampled. Output manifests include
`source_width`, `source_height`, prepared `width`, prepared `height`,
`downsampled`, `downsample_scale`, `max_input_pixels`, and `max_input_side` so
callers can tell when graceful degradation occurred.

### Runtime bootstrap

The installed plugin does not require the current working directory to be a Git
checkout. The MCP server creates Python and Node runtime caches under
`~/.cache/svg-vectorizer-codex-plugin` and writes artifacts to the `output_dir`
passed to the tool.

Before reusing an existing cached venv, the server checks that core modules
(`cv2`, `numpy`, `PIL`, `skimage`, and `vtracer`) import successfully. An
incomplete venv is repaired with `pip install -r requirements.txt`; if repair or
rebuild fails, the bad venv is removed so the next run does not blindly reuse it.
If setup still fails, the error includes the cache venv path, selected Python,
missing modules, and recovery steps. MCP Python tool calls are bounded to 120
seconds and report a timeout with the tool name and runtime venv path. If the
failure is Python selection, set `SVG_VECTORIZER_PYTHON` as described below.

### CLI failures

CLI argument errors exit with code `2` and print argparse usage. Runtime tool
errors, such as a missing input image or invalid manifest, exit with code `1`
and print `error: ...` to stderr without a Python traceback. The legacy
`pipeline_cli.py --tool ... --input-json ...` route prints JSON to stderr on
runtime failures so MCP callers can surface structured diagnostics.

Batch runs return exit code `0` when the batch itself completes, even if one or
more images fail; inspect `failed` and failed item records in
`batch_manifest.json`. A batch with no matching supported images exits with
code `1`.

### Python versions

The bootstrapper supports Python 3.10 through 3.12 for creating the plugin venv.
Newer Python versions, such as 3.14, can make packages such as scikit-image fall
back to native builds on fresh Windows machines without C/C++ Build Tools.

Install Python 3.10, 3.11, or 3.12, or point the plugin at a known supported
interpreter:

```powershell
$env:SVG_VECTORIZER_PYTHON = "C:\path\to\python-3.12.13\python.exe"
```

After an explicit Python 3.12 candidate, the bootstrapper checks the Codex
bundled runtime at
`~/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe`
before falling back to lower supported system versions and broad `python`
candidates. You can still set `SVG_VECTORIZER_PYTHON` explicitly, then start a
new Codex thread so the MCP server inherits the environment.

### Optional renderer dependencies

The SVG renderer uses `@resvg/resvg-js`, whose native platform package is an
optional npm dependency. Do not omit optional dependencies when reinstalling the
renderer runtime manually:

```powershell
npm install @resvg/resvg-js@2.6.2 --include=optional --omit=dev --no-audit --no-fund
```

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full contributor workflow and
[docs/architecture.md](docs/architecture.md) for the MCP, CLI, runtime cache,
and pipeline architecture.

Windows PowerShell:

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -r plugins\svg-vectorizer\server\requirements.txt
.\.venv\Scripts\python -m pip install -e .
.\.venv\Scripts\python -m unittest tests.test_cli -v
.\.venv\Scripts\python -m unittest tests.test_pipeline -v
.\.venv\Scripts\python -m unittest tests.test_mcp_smoke -v
.\.venv\Scripts\python -m unittest tests.test_plugin_manifests -v
.\.venv\Scripts\python -m unittest tests.test_codex_e2e -v
.\.venv\Scripts\python -m unittest tests.test_claude_code_e2e -v
```

POSIX shells:

```sh
python3.10 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r plugins/svg-vectorizer/server/requirements.txt
python -m pip install -e .
python -m unittest tests.test_cli -v
python -m unittest tests.test_pipeline -v
python -m unittest tests.test_mcp_smoke -v
python -m unittest tests.test_plugin_manifests -v
python -m unittest tests.test_codex_e2e -v
python -m unittest tests.test_claude_code_e2e -v
```

Optional: validate the plugin manifest with the `validate_plugin.py` helper from
Codex's system `plugin-creator` skill. The helper is provided by Codex, not this
repository, and is not required for the local test commands above. If you have
that skill installed, point `PLUGIN_VALIDATOR` at your local copy first.

```powershell
$env:PLUGIN_VALIDATOR = Join-Path $env:USERPROFILE ".codex\skills\.system\plugin-creator\scripts\validate_plugin.py"
python $env:PLUGIN_VALIDATOR plugins\svg-vectorizer
```

```sh
PLUGIN_VALIDATOR="$HOME/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py"
python "$PLUGIN_VALIDATOR" plugins/svg-vectorizer
```
