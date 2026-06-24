# Claude Code End-to-End Verification

This note records the real end-to-end verification of the Claude Code plugin
shell: the same input runs through the Claude Code launch path and produces a
consistent SVG. It is reproducible from a checkout via the test below.

## What is verified

1. **The Claude Code launch path runs the pipeline.** The plugin is launched
   exactly as Claude Code launches it — the inline `mcpServers` block in
   [`.claude-plugin/plugin.json`](../../plugins/svg-vectorizer/.claude-plugin/plugin.json)
   with `${CLAUDE_PLUGIN_ROOT}` resolved to the installed plugin directory — and
   a real `run_svg_pipeline` JSON-RPC call returns a real SVG plus validation
   metrics.
2. **Same input → consistent output.** Running the same image and arguments
   through the Claude Code shell and straight through the shared Python core
   yields a byte-identical SVG. The pipeline is deterministic, so repeated runs
   reproduce the same bytes.

## Why this also covers cross-platform consistency

Both platform shells invoke the *same* core script,
`plugins/svg-vectorizer/server/mcp-server.cjs` → `pipeline_cli.py`:

- Codex wires it through [`.mcp.json`](../../plugins/svg-vectorizer/.mcp.json)
  (`./server/mcp-server.cjs` with a plugin-root `cwd`).
- Claude Code wires it inline via `${CLAUDE_PLUGIN_ROOT}/server/mcp-server.cjs`.

`tests/test_plugin_manifests.py` locks that both shells target this single
script and that `server/` is never duplicated. Because the Claude Code shell is
proven here to be a byte-faithful pass-through to that shared core, the same
input produces the same SVG on either platform; any platform difference would
therefore be a launch/manifest difference, which the manifest tests guard.

## Reproduce

From a checkout with the runtime installed (see the README install steps):

```sh
python -m unittest tests.test_claude_code_e2e -v
```

The suite:

- resolves the Claude Code launch command and asserts it points at the shared
  core,
- drives `run_svg_pipeline` over stdio through that command on
  `tests/fixtures/warm_icon.png` and checks the SVG, manifest, path count, and
  validation metrics, and
- compares the shell output against the shared core for
  `tests/fixtures/transparent_icon.png` and asserts byte-identical SVG.

## Observed result

`run_svg_pipeline` on `tests/fixtures/warm_icon.png` (`mode: vtracer`,
`mask_mode: warm-icon`) through the Claude Code launch path:

- `path_count: 4`, fills `#2A2018` / `#FF961E`, SVG ~2.6 KB
- validation `status: warn`, renderer `@resvg/resvg-js`
- metrics: `alpha_iou ≈ 0.92`, `rgba_ssim ≈ 0.78`

The shell vs. shared-core comparison on `tests/fixtures/transparent_icon.png`
(`mode: pixel`, `mask_mode: alpha`) produced byte-identical SVG.

When the optional `@resvg/resvg-js` renderer is unavailable, validation reports
`status: degraded` with the prepared-PNG proxy instead of failing; the converted
SVG is still produced.
