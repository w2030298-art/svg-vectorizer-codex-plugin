from __future__ import annotations

import argparse
import json
import sys

from svg_vectorizer_pipeline import (
    convert_image_to_svg,
    repair_svg_trace,
    run_svg_pipeline,
    validate_svg_trace,
)


TOOLS = {
    "convert_image_to_svg": convert_image_to_svg,
    "validate_svg_trace": validate_svg_trace,
    "repair_svg_trace": repair_svg_trace,
    "run_svg_pipeline": run_svg_pipeline,
}


def _print_json(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False))


def _legacy_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tool", required=True, choices=sorted(TOOLS))
    parser.add_argument("--input-json", required=True)
    args = parser.parse_args(argv)
    try:
        payload = json.loads(args.input_json)
        result = TOOLS[args.tool](**payload)
    except Exception as exc:
        print(json.dumps({"error": str(exc), "type": type(exc).__name__}, ensure_ascii=False), file=sys.stderr)
        return 1
    _print_json(result)
    return 0


def _add_conversion_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--mode", choices=("vtracer", "pixel"), default="vtracer")
    parser.add_argument("--mask-mode", choices=("auto", "alpha", "flood", "warm-icon", "none"), default="auto")
    parser.add_argument("--quality-profile", choices=("compact", "balanced", "fidelity"), default="balanced")


def _build_subcommand_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="svg-vectorizer")
    subcommands = parser.add_subparsers(dest="command", required=True)

    convert = subcommands.add_parser("convert", help="Convert one raster image to one SVG candidate.")
    convert.add_argument("input_path")
    convert.add_argument("output_dir")
    _add_conversion_options(convert)
    convert.add_argument("--name")
    convert.set_defaults(
        handler=lambda args: convert_image_to_svg(
            input_path=args.input_path,
            output_dir=args.output_dir,
            mode=args.mode,
            mask_mode=args.mask_mode,
            quality_profile=args.quality_profile,
            name=args.name,
        )
    )

    validate = subcommands.add_parser("validate", help="Validate an SVG candidate against a source image.")
    validate.add_argument("source_image_path")
    validate.add_argument("svg_path")
    validate.add_argument("output_dir")
    validate.add_argument("--prepared-png-path")
    validate.set_defaults(
        handler=lambda args: validate_svg_trace(
            source_image_path=args.source_image_path,
            svg_path=args.svg_path,
            output_dir=args.output_dir,
            prepared_png_path=args.prepared_png_path,
        )
    )

    repair = subcommands.add_parser("repair", help="Repair a trace by bounded parameter reruns.")
    repair.add_argument("manifest_path")
    repair.add_argument("output_dir")
    repair.add_argument("--budget", type=int, default=6)
    repair.set_defaults(
        handler=lambda args: repair_svg_trace(
            manifest_path=args.manifest_path,
            output_dir=args.output_dir,
            budget=args.budget,
        )
    )

    pipeline = subcommands.add_parser("pipeline", help="Run convert, validate, and optional repair.")
    pipeline.add_argument("input_path")
    pipeline.add_argument("output_dir")
    pipeline.add_argument("--mode", choices=("vtracer", "pixel", "both"), default="vtracer")
    pipeline.add_argument("--mask-mode", choices=("auto", "alpha", "flood", "warm-icon", "none"), default="auto")
    pipeline.add_argument("--quality-profile", choices=("compact", "balanced", "fidelity"), default="balanced")
    pipeline.add_argument("--repair", action="store_true")
    pipeline.set_defaults(
        handler=lambda args: run_svg_pipeline(
            input_path=args.input_path,
            output_dir=args.output_dir,
            mode=args.mode,
            mask_mode=args.mask_mode,
            quality_profile=args.quality_profile,
            repair=args.repair,
        )
    )
    return parser


def _subcommand_main(argv: list[str]) -> int:
    parser = _build_subcommand_parser()
    args = parser.parse_args(argv)
    try:
        result = args.handler(args)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print_json(result)
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--tool" in argv or "--input-json" in argv:
        return _legacy_main(argv)
    return _subcommand_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
