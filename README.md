# SVG Vectorizer Codex Plugin

[![CI](https://github.com/w2030298-art/svg-vectorizer-codex-plugin/actions/workflows/ci.yml/badge.svg)](https://github.com/w2030298-art/svg-vectorizer-codex-plugin/actions/workflows/ci.yml)

Codex plugin for converting raster images to SVG with a convert, validate, and
repair workflow. It defaults to compact editable `vtracer` output, can generate
pixel-fidelity SVGs when exact raster matching matters, and validates traces
without starting browser or HTTP preview services.

## Gallery

See the [real-world gallery](docs/gallery/real-world/README.md) for source
images, vtracer and pixel SVG candidates, diff contact sheets, assessment JSON,
and browser-open review screenshots across logo, icon, warm-icon, and photo
inputs.

## Sources of truth

This README is the current-state snapshot and entry point, not a changelog.

- **Overview · how to run · architecture** -> this README.
- **Contributor architecture** -> [docs/architecture.md](docs/architecture.md).
- **Contributing workflow** -> [CONTRIBUTING.md](CONTRIBUTING.md).
- **Version / what shipped** -> [GitHub Releases](../../releases). The marketplace plugin is currently `v0.1.0`; the first GitHub Release is cut at `v0.2.0`.
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

### Claude Code

Claude Code packaging is planned for a later milestone. The core conversion
pipeline and MCP server already live in `plugins/svg-vectorizer/server`; the
Claude Code adapter should stay thin and reuse the same core rather than forking
tool behavior.

## Agent Routing

The bundled `svg-vectorizer` skill tells agents to route image-to-SVG requests
through the MCP tools in this order:

1. Use `run_svg_pipeline` by default with `mode: "vtracer"` and `mask_mode: "auto"`.
2. Use `mode: "pixel"` only for explicit pixel-level fidelity or exact-match requests.
3. Use `mode: "both"` when comparing vtracer and pixel routes.
4. Run `repair_svg_trace` only when requested or when validation shows the trace needs another bounded parameter pass.

Background handling follows the same skill guidance: `alpha` for sources that
already have transparency, `warm-icon` for orange/black icons on gradient or
shadowed backgrounds, `flood` for mostly flat solid backgrounds, and `none` only
when the full image including the background should be traced.

## Tool Reference

Tools are exposed through MCP in Codex. For local development or reproducible
examples, the same tools can be called with:

```sh
python3 plugins/svg-vectorizer/server/pipeline_cli.py --tool TOOL_NAME --input-json '{"key":"value"}'
```

### `run_svg_pipeline`

Runs conversion, validation, and optional repair in one call. This is the
default entry point for normal requests.

| Parameter | Required | Default | Notes |
| --- | --- | --- | --- |
| `input_path` | yes | - | Raster source path: PNG, JPG, or WebP supported by Pillow. |
| `output_dir` | yes | - | Directory for SVG, prepared PNG, validation artifacts, and `pipeline_manifest.json`. |
| `mode` | no | `vtracer` | `vtracer`, `pixel`, or `both`. `both` writes separate vtracer and pixel candidates. |
| `mask_mode` | no | `auto` | `auto`, `alpha`, `flood`, `warm-icon`, or `none`. |
| `quality_profile` | no | `balanced` | `compact`, `balanced`, or `fidelity`; applies to vtracer output. |
| `repair` | no | `false` | When `true`, runs bounded repair after a non-`both` pipeline. |

Output: a JSON result with the selected mode, artifact paths, SVG structure
stats, validation status and metrics for single-candidate modes, and
`pipeline_manifest.json`.

### `convert_image_to_svg`

Converts one raster image into one SVG candidate.

| Parameter | Required | Default | Notes |
| --- | --- | --- | --- |
| `input_path` | yes | - | Raster source path. |
| `output_dir` | yes | - | Directory for the prepared PNG, SVG, and candidate manifest. |
| `mode` | no | `vtracer` | `vtracer` or `pixel`. |
| `mask_mode` | no | `auto` | `auto`, `alpha`, `flood`, `warm-icon`, or `none`. |
| `quality_profile` | no | `balanced` | `compact`, `balanced`, or `fidelity`. Pixel mode ignores the vtracer settings but should use `fidelity` for clarity. |
| `name` | no | source stem | Optional output stem. |

Output: JSON with `svg`, `prepared_png`, candidate `manifest`, effective
`mask_mode`, image dimensions, foreground pixel count, path/fill counts, and SVG
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
python3 plugins/svg-vectorizer/server/pipeline_cli.py --tool run_svg_pipeline --input-json '{"input_path":"tests/fixtures/warm_icon.png","output_dir":"tmp/readme-vtracer","mode":"vtracer","mask_mode":"warm-icon","quality_profile":"balanced"}'
```

Create vtracer and pixel candidates for comparison:

```sh
python3 plugins/svg-vectorizer/server/pipeline_cli.py --tool run_svg_pipeline --input-json '{"input_path":"tests/fixtures/transparent_icon.png","output_dir":"tmp/readme-both","mode":"both","mask_mode":"alpha","quality_profile":"balanced"}'
```

Validate an existing SVG candidate:

```sh
python3 plugins/svg-vectorizer/server/pipeline_cli.py --tool validate_svg_trace --input-json '{"source_image_path":"tests/fixtures/warm_icon.png","svg_path":"tmp/readme-vtracer/warm_icon_vtracer.svg","prepared_png_path":"tmp/readme-vtracer/warm_icon_prepared.png","output_dir":"tmp/readme-vtracer/validation-again"}'
```

Run bounded repair against a previous pipeline manifest:

```sh
python3 plugins/svg-vectorizer/server/pipeline_cli.py --tool repair_svg_trace --input-json '{"manifest_path":"tmp/readme-vtracer/pipeline_manifest.json","output_dir":"tmp/readme-repair","budget":2}'
```

## Degraded Mode And Troubleshooting

### Renderer unavailable

Validation uses `@resvg/resvg-js` when the Node runtime is available. If renderer
setup fails, validation returns `status: "degraded"` and
`renderer: "prepared-png-proxy"` instead of failing the whole request. In
degraded mode, use structural metrics plus the generated artifacts for review;
exact SVG raster metrics are unavailable until the renderer is installed.

### Runtime bootstrap

The installed plugin does not require the current working directory to be a Git
checkout. The MCP server creates Python and Node runtime caches under
`~/.cache/svg-vectorizer-codex-plugin` and writes artifacts to the `output_dir`
passed to the tool.

If setup fails, remove the cache directory and start a new Codex thread so the
server can bootstrap again. If the failure is Python selection, set
`SVG_VECTORIZER_PYTHON` as described below.

### Python versions

The bootstrapper supports Python 3.10 through 3.12 for creating the plugin venv.
Newer Python versions, such as 3.14, can make packages such as scikit-image fall
back to native builds on fresh Windows machines without C/C++ Build Tools.

Install Python 3.10, 3.11, or 3.12, or point the plugin at a known supported
interpreter:

```powershell
$env:SVG_VECTORIZER_PYTHON = "C:\path\to\python-3.12.13\python.exe"
```

For Codex bundled Python, set `SVG_VECTORIZER_PYTHON` to the bundled Python
3.12.13 `python.exe`, then start a new Codex thread so the MCP server inherits
the environment.

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
.\.venv\Scripts\python -m unittest tests.test_pipeline -v
.\.venv\Scripts\python -m unittest tests.test_mcp_smoke -v
```

POSIX shells:

```sh
python3.10 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r plugins/svg-vectorizer/server/requirements.txt
python -m unittest tests.test_pipeline -v
python -m unittest tests.test_mcp_smoke -v
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
