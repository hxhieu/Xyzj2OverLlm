#!/usr/bin/env python3
"""Export non-db runtime resources from the Postgres translation workflow."""

from __future__ import annotations

import argparse
import re
import os
import sys
import tomllib
from pathlib import Path

import psycopg2


ASSET_FILES = {
    "dumpedPrefabText.txt": "prefab_text",
    "dynamicStrings.txt": "dynamic_string",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export runtime asset resources from Postgres.",
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL"),
        help="Postgres connection string. Defaults to DATABASE_URL, then .codex/config.toml.",
    )
    parser.add_argument(
        "--file",
        choices=sorted(ASSET_FILES),
        default="dumpedPrefabText.txt",
        help="Asset file to export.",
    )
    parser.add_argument(
        "--output",
        default="_working/postgres_export/dumpedPrefabText.txt",
        help="Output path.",
    )
    parser.add_argument(
        "--translated-status",
        action="append",
        default=["locked", "reviewed"],
        choices=["ignored", "pending", "reviewed", "locked"],
        help="Statuses allowed for translated output. Can be repeated.",
    )
    return parser.parse_args()


def load_database_url_from_codex_config() -> str | None:
    config_path = Path(".codex/config.toml")
    if not config_path.exists():
        return None
    config = tomllib.loads(config_path.read_text())
    try:
        return config["mcp_servers"]["postgres"]["env"]["DATABASE_URI"]
    except KeyError:
        return None


def escape_resource_text(text: str) -> str:
    return text.replace("\r", "").replace("\n", "\\n")


def double_quote(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def replace_commas_in_brackets(text: str, replacement: str) -> str:
    output = []
    depth = 0
    for char in text:
        if char in "<[{":
            depth += 1
        if char in ">]}":
            depth -= 1
        if char == "," and depth > 0:
            output.append(replacement)
        else:
            output.append(char)
    return "".join(output)


def prepare_method_parameters(raw_parameters: str) -> list[str]:
    if len(raw_parameters) >= 2 and raw_parameters[0] == "[" and raw_parameters[-1] == "]":
        raw = raw_parameters[1:-1]
    else:
        raw = raw_parameters
    raw = raw.replace("，", ",")
    replacement = "，"
    raw = replace_commas_in_brackets(raw, replacement)
    parts = re.split(r",(?![^\[\]{}<>]*[\]\}>])", raw)
    return [part.replace(replacement, ",") for part in parts]


def export_prefab(cursor, args, kind: str, output_path: Path) -> tuple[int, int]:
    exported = 0
    untranslated = 0
    cursor.execute(
        """
        select
            ae.entry_no,
            ae.source_text,
            coalesce(tov.translated_text, tv.translated_text) as translated_text,
            coalesce(tov.status, toc.status, tv.status) as export_status
        from asset_entries ae
        join translation_occurrences toc
          on toc.asset_entry_id = ae.id
        join translation_values tv
          on tv.id = toc.translation_value_id
        left join translation_overrides tov
          on tov.occurrence_id = toc.id
         and tov.status = any(%s::translation_status[])
        where ae.kind = %s::occurrence_kind
          and ae.source_name = %s
        order by ae.entry_no
        """,
        (args.translated_status, kind, args.file),
    )
    with output_path.open("w", encoding="utf-8", newline="\n") as output:
        for _, source_text, translated_text, export_status in cursor:
            if export_status not in args.translated_status or not translated_text:
                untranslated += 1
                continue
            output.write(f"- raw: {escape_resource_text(source_text)}\n")
            output.write(f"  result: {escape_resource_text(translated_text)}\n")
            exported += 1
    return exported, untranslated


def export_dynamic(cursor, args, kind: str, output_path: Path) -> tuple[int, int]:
    exported = 0
    untranslated = 0
    cursor.execute(
        """
        select
            ae.entry_no,
            ae.source_text,
            ae.raw_payload,
            coalesce(tov.translated_text, tv.translated_text) as translated_text,
            coalesce(tov.status, toc.status, tv.status) as export_status
        from asset_entries ae
        join translation_occurrences toc
          on toc.asset_entry_id = ae.id
        join translation_values tv
          on tv.id = toc.translation_value_id
        left join translation_overrides tov
          on tov.occurrence_id = toc.id
         and tov.status = any(%s::translation_status[])
        where ae.kind = %s::occurrence_kind
          and ae.source_name = %s
        order by ae.entry_no
        """,
        (args.translated_status, kind, args.file),
    )
    with output_path.open("w", encoding="utf-8", newline="\n") as output:
        for _, source_text, payload, translated_text, export_status in cursor:
            if export_status not in args.translated_status or not translated_text:
                untranslated += 1
                continue

            parameters = prepare_method_parameters(payload["parameters"])
            output.write(f"- type: {double_quote(payload['type'])}\n")
            output.write(f"  method: {double_quote(payload['method'])}\n")
            output.write(f"  iLOffset: {payload['iLOffset']}\n")
            output.write(f"  raw: {double_quote(source_text)}\n")
            output.write(f"  translation: {double_quote(translated_text)}\n")
            output.write("  parameters:\n")
            for parameter in parameters:
                output.write(f"  - {double_quote(parameter)}\n")
            exported += 1
    return exported, untranslated


def main() -> int:
    args = parse_args()
    database_url = args.database_url or load_database_url_from_codex_config()
    if not database_url:
        print(
            "Missing database URL. Set DATABASE_URL or pass --database-url.",
            file=sys.stderr,
        )
        return 2

    kind = ASSET_FILES[args.file]
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with psycopg2.connect(database_url) as conn:
        with conn.cursor(name="asset_export") as cursor:
            if kind == "prefab_text":
                exported, untranslated = export_prefab(cursor, args, kind, output_path)
            elif kind == "dynamic_string":
                exported, untranslated = export_dynamic(cursor, args, kind, output_path)
            else:
                raise ValueError(f"Unsupported asset kind: {kind}")

    print(f"source_file: {args.file}")
    print(f"kind: {kind}")
    print(f"output: {output_path}")
    print(f"exported_entries: {exported}")
    print(f"skipped_untranslated: {untranslated}")
    print(f"size_bytes: {output_path.stat().st_size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
