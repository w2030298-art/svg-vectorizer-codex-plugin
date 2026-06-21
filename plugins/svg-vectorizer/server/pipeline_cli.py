from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tool", required=True, choices=sorted(TOOLS))
    parser.add_argument("--input-json", required=True)
    args = parser.parse_args()
    payload = json.loads(args.input_json)
    result = TOOLS[args.tool](**payload)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"error": str(exc), "type": type(exc).__name__}), file=sys.stderr)
        raise
