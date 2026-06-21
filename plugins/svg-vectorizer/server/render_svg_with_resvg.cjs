#!/usr/bin/env node
"use strict";

const fs = require("fs");
const { Resvg } = require("@resvg/resvg-js");

const [svgPath, outputPath, widthRaw, heightRaw] = process.argv.slice(2);
if (!svgPath || !outputPath || !widthRaw || !heightRaw) {
  console.error("usage: render_svg_with_resvg.cjs <svg> <output.png> <width> <height>");
  process.exit(2);
}

const width = Number(widthRaw);
const height = Number(heightRaw);
const svg = fs.readFileSync(svgPath);
const resvg = new Resvg(svg, {
  background: "rgba(0, 0, 0, 0)",
  fitTo: { mode: "width", value: width },
});
const png = resvg.render().asPng();
fs.mkdirSync(require("path").dirname(outputPath), { recursive: true });
fs.writeFileSync(outputPath, png);
