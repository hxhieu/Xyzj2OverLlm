#!/usr/bin/env python3
"""Extract compiled dialogue graph TextAssets from Unity resources.assets."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


DEFAULT_ASSETS = Path("_working/nextstopjianghu2_data/resources.assets")
DEFAULT_OUTPUT_DIR = Path("_working/extracted_scripts")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Extract compiled graph TextAsset .bytes files from Unity resources.assets. "
            "Use --name for exact TextAsset names or --contains for substring filters."
        ),
    )
    parser.add_argument(
        "--assets",
        type=Path,
        default=DEFAULT_ASSETS,
        help=f"Path to resources.assets. Default: {DEFAULT_ASSETS}",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Directory for extracted .bytes files. Default: {DEFAULT_OUTPUT_DIR}",
    )
    parser.add_argument(
        "--name",
        action="append",
        default=[],
        help="Exact TextAsset name to extract. Can be repeated.",
    )
    parser.add_argument(
        "--contains",
        action="append",
        default=[],
        help="Substring filter for TextAsset names. Can be repeated.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Extract every TextAsset. This is usually noisy; prefer --name or --contains.",
    )
    parser.add_argument(
        "--list-only",
        action="store_true",
        help="List matched TextAssets without writing files.",
    )
    return parser.parse_args()


def load_unitypy():
    try:
        import UnityPy  # type: ignore
    except ImportError:
        print(
            "UnityPy is not installed. Install it in a local venv, for example:\n"
            "  python3 -m venv _working/venv-unitypy\n"
            "  _working/venv-unitypy/bin/pip install UnityPy\n"
            "Then run this script with _working/venv-unitypy/bin/python.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return UnityPy


def script_to_bytes(script: object) -> bytes:
    if isinstance(script, bytes):
        return script
    if isinstance(script, bytearray):
        return bytes(script)
    if isinstance(script, str):
        # UnityPy decodes arbitrary bytes into a Python str containing surrogate
        # escapes. This preserves binary graph bytes such as fe ff.
        return script.encode("utf-8", "surrogateescape")
    if script is None:
        return b""
    raise TypeError(f"Unsupported TextAsset script type: {type(script)!r}")


def matches(name: str, exact_names: set[str], contains: list[str], all_assets: bool) -> bool:
    if all_assets:
        return True
    if exact_names and name in exact_names:
        return True
    return any(part in name for part in contains)


def main() -> int:
    args = parse_args()
    if not args.assets.exists():
        print(f"resources.assets not found: {args.assets}", file=sys.stderr)
        return 1
    if not args.all and not args.name and not args.contains:
        print("No filters supplied. Use --name, --contains, or --all.", file=sys.stderr)
        return 2

    UnityPy = load_unitypy()
    env = UnityPy.load(str(args.assets))
    args.output_dir.mkdir(parents=True, exist_ok=True)

    exact_names = set(args.name)
    matched = 0
    written = 0

    for obj in env.objects:
        if obj.type.name != "TextAsset":
            continue
        data = obj.read()
        name = getattr(data, "m_Name", "") or getattr(data, "name", "") or ""
        if not matches(name, exact_names, args.contains, args.all):
            continue

        script = getattr(data, "m_Script", None)
        if script is None:
            script = getattr(data, "script", b"")
        raw = script_to_bytes(script)
        matched += 1
        print(f"{name}\t{len(raw)} bytes\tpath_id={obj.path_id}")

        if not args.list_only:
            out_path = args.output_dir / f"{name}.bytes"
            out_path.write_bytes(raw)
            written += 1

    if matched == 0:
        print("No matching TextAssets found.", file=sys.stderr)
        return 1

    if args.list_only:
        print(f"Matched {matched} TextAsset(s).")
    else:
        print(f"Wrote {written} file(s) to {args.output_dir}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
