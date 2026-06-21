---
name: svg-vectorizer
description: Convert raster images into SVG using a conversion-validation-repair workflow. Use when the user asks to turn PNG/JPG/WebP images into SVG, compare vtracer vs pixel tracing, validate SVG fidelity, remove raster backgrounds before tracing, or repair a vector trace.
---

# SVG Vectorizer

Use the bundled `svgVectorizer` MCP tools for image-to-SVG work.

## Route

Follow this order:

1. Convert
2. Validate
3. Repair, only when requested or when validation fails

Default to `run_svg_pipeline` with `mode: "vtracer"` and `mask_mode: "auto"`.

## Conversion Choice

- Use `vtracer` by default for clean, editable, compact SVGs.
- Use `pixel` only when the user explicitly asks for pixel-level fidelity, an exact match, or a high-fidelity fallback.
- Use `both` when the user asks to compare routes.
- Use `mask_mode: "warm-icon"` for orange/black icons on gradient or shadowed backgrounds.
- Use `mask_mode: "alpha"` when the source already has transparency.
- Use `mask_mode: "flood"` for mostly flat solid backgrounds.
- Use `mask_mode: "none"` only when the full image, including background, should be traced.

## Validation

Do not start browser or HTTP preview services by default.

Use `validate_svg_trace` to produce:

- structural SVG metrics
- prepared transparent source
- diff/contact sheet
- LLM-facing `assessment.json`

If validation reports `degraded`, explain that exact SVG raster metrics were unavailable and rely on structural metrics plus generated artifacts.

## Repair

Use `repair_svg_trace` for bounded parameter reruns. Do not directly edit SVG paths unless the user explicitly asks for manual path surgery.

Prefer concise reports:

- chosen mode
- mask mode
- output SVG path
- key metrics
- whether repair was run
- remaining tradeoffs
