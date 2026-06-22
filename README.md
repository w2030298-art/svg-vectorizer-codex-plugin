# SVG Vectorizer Codex Plugin

[![CI](https://github.com/w2030298-art/svg-vectorizer-codex-plugin/actions/workflows/ci.yml/badge.svg)](https://github.com/w2030298-art/svg-vectorizer-codex-plugin/actions/workflows/ci.yml)

Codex plugin for converting raster images to SVG with a three-stage workflow:

1. Convert: vtracer by default, pixel SVG on explicit fidelity requests.
2. Validate: browserless image and SVG structure checks with cv2/skimage.
3. Repair: optional parameter reruns, not manual SVG path edits.

## Install from this repository

```powershell
codex plugin marketplace add w2030298-art/svg-vectorizer-codex-plugin --ref main
codex plugin add svg-vectorizer@svg-tools
```

Start a new Codex thread after installing the plugin.

On first use, the MCP server creates runtime caches under
`~/.cache/svg-vectorizer-codex-plugin`:

- a Python venv with vtracer, OpenCV, scikit-image, Pillow, and NumPy
- a Node runtime folder with `@resvg/resvg-js` for browserless SVG rendering

If either runtime setup fails, the tool returns a degraded validation report
instead of starting a browser or leaving a background preview server running.

## Troubleshooting fresh environments

### Supported Python versions

The bootstrapper supports Python 3.10 through 3.12 for creating the plugin
venv. Python versions newer than that, such as 3.14, can make packages such as
scikit-image fall back to native builds on fresh Windows machines without C/C++
Build Tools.

Install Python 3.10, 3.11, or 3.12, or point the plugin at a known supported
interpreter:

```powershell
$env:SVG_VECTORIZER_PYTHON = "C:\path\to\python-3.12.13\python.exe"
```

For Codex bundled Python, set `SVG_VECTORIZER_PYTHON` to the bundled Python
3.12.13 `python.exe`, then start a new Codex thread so the MCP server inherits
the environment.

### Fresh empty directories

The installed plugin does not require the current working directory to be a Git
checkout. It creates Python and Node runtime caches under
`~/.cache/svg-vectorizer-codex-plugin` and writes artifacts to the `output_dir`
you pass to the tool.

### Renderer optional dependencies

The SVG renderer uses `@resvg/resvg-js`, whose native platform package is an
optional npm dependency. Do not omit optional dependencies when reinstalling the
renderer runtime manually:

```powershell
npm install @resvg/resvg-js@2.6.2 --include=optional --omit=dev --no-audit --no-fund
```

## Local development

Windows PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -r plugins\svg-vectorizer\server\requirements.txt
.\.venv\Scripts\python -m unittest tests.test_pipeline -v
.\.venv\Scripts\python -m unittest tests.test_mcp_smoke -v
```

POSIX shells:

```sh
python3 -m venv .venv
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
