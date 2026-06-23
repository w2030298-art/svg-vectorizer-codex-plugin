# Architecture

This document explains the current contributor-facing architecture. Start with
the [README](../README.md) for user-facing installation, tool reference, and
examples. Use [CONTRIBUTING](../CONTRIBUTING.md) for local development and PR
workflow.

## Repository Shape

The repository ships **one core and two thin platform shells** that both point
at it. `plugins/svg-vectorizer/server/` is the single core and is never
duplicated; the shells only adapt manifest format and MCP path resolution.

- `plugins/svg-vectorizer/.codex-plugin/plugin.json` is the Codex plugin
  manifest. It points Codex at the bundled skills and MCP server config.
- `plugins/svg-vectorizer/.mcp.json` is the Codex MCP wiring. It registers the
  `svgVectorizer` MCP server and starts `node ./server/mcp-server.cjs` from the
  plugin directory using a plugin-root `cwd`.
- `plugins/svg-vectorizer/.claude-plugin/plugin.json` is the Claude Code plugin
  manifest. It wires the same `svgVectorizer` server inline with
  `${CLAUDE_PLUGIN_ROOT}/server/mcp-server.cjs`, which is how Claude Code
  resolves plugin-root paths. Skills are auto-discovered from `skills/`, so the
  manifest does not duplicate the skill tree.
- `.claude-plugin/marketplace.json` (repository root) registers this repo as the
  `svg-tools` Claude Code marketplace so
  `claude plugin install svg-vectorizer@svg-tools` works.
- `plugins/svg-vectorizer/skills/svg-vectorizer/SKILL.md` is the single agent
  routing guide for image-to-SVG requests, shared by both shells.
- `plugins/svg-vectorizer/server/mcp-server.cjs` is the Node MCP runtime and
  bootstrapper.
- `plugins/svg-vectorizer/server/pipeline_cli.py` is the local CLI shim. It
  exposes human-oriented subcommands and the legacy JSON payload route used by
  MCP.
- `plugins/svg-vectorizer/server/svg_vectorizer_pipeline.py` contains the core
  conversion, validation, repair, and batch orchestration logic.
- `plugins/svg-vectorizer/server/render_svg_with_resvg.cjs` renders SVGs to PNG
  through `@resvg/resvg-js` for validation metrics.
- `tests/` covers the Python pipeline, MCP smoke behavior, and the dual-shell
  manifest invariants. `docs/gallery/` stores source-backed gallery evidence,
  not runtime state.

## MCP Data Flow

The installed plugin flow is identical from step 3 onward on both platforms;
only the manifest and MCP wiring differ in steps 1-2:

1. **Codex** loads `.codex-plugin/plugin.json`, then
   `plugins/svg-vectorizer/.mcp.json`, and starts `mcp-server.cjs` with a
   plugin-root `cwd`. **Claude Code** loads `.claude-plugin/plugin.json`, whose
   inline `mcpServers` starts the same `mcp-server.cjs` via
   `${CLAUDE_PLUGIN_ROOT}/server/mcp-server.cjs`.
2. Either host runs the single `mcp-server.cjs` core as the `svgVectorizer` MCP
   server.
3. The agent skill chooses one of the MCP tools:
   `run_svg_pipeline`, `convert_image_to_svg`, `validate_svg_trace`, or
   `repair_svg_trace`.
4. `mcp-server.cjs` receives JSON-RPC `tools/call`, validates the tool name, and
   calls `pipeline_cli.py --tool TOOL --input-json PAYLOAD`.
5. `pipeline_cli.py` deserializes the payload and dispatches to
   `svg_vectorizer_pipeline.py`.
6. The Python pipeline writes artifacts under the caller-provided `output_dir`
   and returns JSON. The MCP server wraps that JSON as text content in the MCP
   response.

For local development, the legacy JSON CLI starts at step 4 and bypasses MCP
bootstrap:

```sh
python plugins/svg-vectorizer/server/pipeline_cli.py --tool run_svg_pipeline --input-json '{"input_path":"tests/fixtures/warm_icon.png","output_dir":"tmp/architecture-smoke","mode":"vtracer","mask_mode":"warm-icon"}'
```

## Runtime Bootstrap

The MCP server is intentionally the only bootstrap layer. The Python pipeline
does not create virtual environments on its own.

- Cache root: `~/.cache/svg-vectorizer-codex-plugin`
- Python runtime: `~/.cache/svg-vectorizer-codex-plugin/venv`
- Node renderer runtime:
  `~/.cache/svg-vectorizer-codex-plugin/node-runtime`

On first tool use, `mcp-server.cjs` selects Python 3.10 through 3.12, creates
the cache venv, and installs
`plugins/svg-vectorizer/server/requirements.txt`. Set
`SVG_VECTORIZER_PYTHON` to force a supported interpreter.

Existing cache venvs are health checked before reuse by importing the core
modules: `cv2`, `numpy`, `PIL`, `skimage`, and `vtracer`. If any are missing,
the server tries to repair the venv with `pip install -r requirements.txt`; if
repair or rebuild fails, it removes the bad venv so later runs do not reuse a
partial environment. Python discovery tries an explicit 3.12 first, then checks
Codex's bundled runtime at
`~/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe`
before falling back to lower supported system versions and finally failing on an
unsupported broad `python` or `py -3` candidate.

Validation also needs `@resvg/resvg-js`. The MCP server installs it into the
Node runtime cache with optional native dependencies included. If renderer setup
fails, validation returns `status: "degraded"` and uses the prepared PNG as a
proxy so conversion can still complete.

Local tests and local CLI runs use the active developer environment. Renderer
enabled validation is controlled by:

- `SVG_VECTORIZER_RENDER_HELPER`
- `SVG_VECTORIZER_NODE_MODULES`
- `SVG_VECTORIZER_RENDER_SETUP_ERROR`

## Pipeline Data Flow

`run_svg_pipeline` is the default high-level path:

1. `convert_image_to_svg` loads the raster source with Pillow and prepares a
   transparent PNG.
2. The selected conversion mode writes one SVG candidate:
   - `vtracer` produces compact editable vector paths.
   - `pixel` groups same-color horizontal pixel runs into SVG paths for
     explicit pixel-fidelity requests.
3. Single-candidate runs call `validate_svg_trace`, which renders the SVG when
   possible, computes metrics, writes a diff contact sheet, and emits an
   assessment JSON.
4. If `repair` is true for a single-candidate run, `repair_svg_trace` reruns
   bounded vtracer quality profiles and selects the best candidate.
5. The pipeline writes `pipeline_manifest.json` in `output_dir`.

`mode: "both"` writes separate `vtracer` and `pixel` candidates and a pipeline
manifest for comparison. It does not run validation or repair automatically.

`run_batch_pipeline` wraps `run_svg_pipeline` for directory, file, or glob
inputs. It writes one subdirectory per image, preserves per-image pipeline
manifests, isolates individual image failures, and writes a top-level
`batch_manifest.json` with success/failure counts and key metrics. Worker
parallelism is bounded by `max_workers` so batch runs do not consume every CPU.

## Artifacts

The pipeline writes all generated files below the requested `output_dir`:

- prepared transparent PNG
- SVG candidate
- candidate manifest JSON
- validation rendered PNG when the renderer is available
- validation diff PNG/contact sheet
- validation assessment JSON
- pipeline manifest JSON
- batch manifest JSON when batch mode is used
- repair report JSON when repair is requested

Generated outputs belong outside tracked source paths unless they are deliberate
evidence, such as the committed real-world gallery.

## Core Algorithms

Background handling is explicit because trace quality depends on foreground
selection:

- `alpha` uses source transparency.
- `flood` estimates the border background color and flood-fills connected
  background pixels.
- `warm-icon` isolates orange/black icon foregrounds on gradient or shadowed
  backgrounds.
- `checkerboard` detects baked light gray/white checkerboard backgrounds in
  RGB/no-alpha inputs and makes those pixels transparent before tracing.
- `none` treats every pixel as foreground.
- `auto` chooses alpha when transparency exists, otherwise flood. It does not
  select `checkerboard`, which keeps the WEN-426 strategy opt-in for backwards
  compatibility.

Vtracer profiles are fixed in `QUALITY_PROFILES`: `compact`, `balanced`, and
`fidelity`. Pixel mode ignores those profiles and writes exact horizontal
same-color runs with `shape-rendering="crispEdges"`.

Validation compares the prepared source RGBA to the rendered SVG RGBA. The
primary metrics are alpha IoU, RGBA SSIM, mean absolute RGBA delta, foreground
pixel counts, and SVG structure stats. Repair is a bounded parameter rerun, not
manual path editing; candidates are scored by raster metrics with a small path
count penalty.

## Version Records

Version history lives in [GitHub Releases](../../releases). Release notes are
derived from merged PRs with:

```sh
gh release create vX.Y.Z --generate-notes
```

Categories are configured in `.github/release.yml`. Do not add a hand-maintained
`CHANGELOG.md`; if one is ever needed inside the repository, it must be only a
thin pointer to Releases or generated from Releases and marked as derived,
non-authoritative.
