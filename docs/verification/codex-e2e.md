# Codex End-to-End Verification

This note records the real end-to-end verification of the Codex plugin shell:
the same input runs through the Codex launch path and produces a consistent
SVG. It is reproducible from a checkout via the test below.

## What is verified

1. **The Codex launch path runs the pipeline.** The plugin is launched through
   [`.codex-plugin/plugin.json`](../../plugins/svg-vectorizer/.codex-plugin/plugin.json)
   and [`.mcp.json`](../../plugins/svg-vectorizer/.mcp.json), using the MCP
   server `cwd` exactly as Codex does. A real `run_svg_pipeline` JSON-RPC call
   returns a real SVG plus validation metrics.
2. **Same input -> consistent output.** Running the same image and arguments
   through the Codex shell and straight through the shared Python core yields a
   byte-identical SVG. The pipeline is deterministic, so repeated runs
   reproduce the same bytes.

## Why this covers the Codex side

Codex wires the shared core as:

- `.codex-plugin/plugin.json` -> `./.mcp.json`
- `.mcp.json` -> `node ./server/mcp-server.cjs` with plugin-root `cwd`

`tests/test_codex_e2e.py` resolves that path from the manifests instead of
hard-coding the server script. Any Codex manifest or MCP wiring drift therefore
breaks the launch test before the pipeline comparison runs.

## Reproduce

From a checkout with the runtime installed (see the README install steps):

```sh
python -m unittest tests.test_codex_e2e -v
```

The suite:

- resolves the Codex launch command and asserts it points at the shared core
  with the plugin-root `cwd`,
- drives `run_svg_pipeline` over stdio through that command on
  `tests/fixtures/warm_icon.png` and checks the SVG, manifest, path count, and
  validation metrics, and
- compares the Codex shell output against the shared core for
  `tests/fixtures/transparent_icon.png` and asserts byte-identical SVG.

## Observed result

The Codex launch path produced a real SVG and validation metrics for
`tests/fixtures/warm_icon.png` (`mode: vtracer`, `mask_mode: warm-icon`), and
the shell vs. shared-core comparison on `tests/fixtures/transparent_icon.png`
(`mode: pixel`, `mask_mode: alpha`) produced byte-identical SVG.

When the optional `@resvg/resvg-js` renderer is unavailable, validation reports
`status: degraded` with the prepared-PNG proxy instead of failing; the converted
SVG is still produced.