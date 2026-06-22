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

## Local development

```powershell
py -m venv .venv
.\.venv\Scripts\python -m pip install -r plugins\svg-vectorizer\server\requirements.txt
.\.venv\Scripts\python -m unittest tests.test_pipeline tests.test_mcp_smoke -v
```

Validate the plugin manifest:

```powershell
python C:\Users\22003\.codex\skills\.system\plugin-creator\scripts\validate_plugin.py plugins\svg-vectorizer
```
