#!/usr/bin/env python3
"""Export a full expanded db1.txt from the Postgres translation workflow."""

from __future__ import annotations

import argparse
import os
import sys
import tomllib
from pathlib import Path

import psycopg2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export full db1.txt from Postgres source shape and translations.",
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL"),
        help="Postgres connection string. Defaults to DATABASE_URL, then .codex/config.toml.",
    )
    parser.add_argument(
        "--import-id",
        type=int,
        default=None,
        help="db_imports.id to export. Defaults to workflow_metadata.active_db_import.",
    )
    parser.add_argument(
        "--output",
        default="_working/postgres_export/db1.txt",
        help="Output db1.txt path.",
    )
    parser.add_argument(
        "--translated-status",
        action="append",
        default=["locked", "reviewed"],
        choices=["ignored", "pending", "reviewed", "locked"],
        help="Statuses allowed for translated output. Can be repeated.",
    )
    parser.add_argument(
        "--require-occurrence-status",
        action="store_true",
        help=(
            "Only export translations for occurrences whose own status is allowed. "
            "Default applies allowed deduped values to all matching occurrences."
        ),
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


def active_import_id(cursor) -> int:
    cursor.execute(
        """
        select (value->>'import_id')::bigint
        from workflow_metadata
        where key = 'active_db_import'
        """
    )
    row = cursor.fetchone()
    if not row or row[0] is None:
        raise ValueError("workflow_metadata.active_db_import is missing")
    return int(row[0])


def translated_fields(
    cursor,
    import_id: int,
    statuses: list[str],
    require_occurrence_status: bool,
):
    status_filter = (
        "toc.status = any(%s::translation_status[])"
        if require_occurrence_status
        else "coalesce(tov.status, tv.status) = any(%s::translation_status[])"
    )
    cursor.execute(
        f"""
        select
            ds.section_order,
            dl.line_no,
            df.field_index,
            coalesce(tov.translated_text, tv.translated_text) as translated_text
        from translation_occurrences toc
        join db_fields df
          on df.id = toc.db_field_id
        join db_lines dl
          on dl.id = df.line_id
        join db_sections ds
          on ds.id = dl.section_id
        join translation_values tv
          on tv.id = toc.translation_value_id
        left join translation_overrides tov
          on tov.occurrence_id = toc.id
         and tov.status = any(%s::translation_status[])
        where ds.import_id = %s
          and toc.kind = 'db_field'::occurrence_kind
          and {status_filter}
          and coalesce(tov.translated_text, tv.translated_text) is not null
          and coalesce(tov.translated_text, tv.translated_text) <> ''
        order by ds.section_order, dl.line_no, df.field_index
        """,
        (statuses, import_id, statuses),
    )
    current_key: tuple[int, int] | None = None
    current_fields: dict[int, str] = {}
    for section_order, line_no, field_index, translated_text in cursor:
        key = (section_order, line_no)
        if current_key is None:
            current_key = key
        if key != current_key:
            yield current_key, current_fields
            current_key = key
            current_fields = {}
        current_fields[field_index] = translated_text
    if current_key is not None:
        yield current_key, current_fields


def main() -> int:
    args = parse_args()
    database_url = args.database_url or load_database_url_from_codex_config()
    if not database_url:
        print(
            "Missing database URL. Set DATABASE_URL or pass --database-url.",
            file=sys.stderr,
        )
        return 2

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with psycopg2.connect(database_url) as conn:
        with conn.cursor(name="export_lines") as line_cursor:
            with conn.cursor(name="export_translations") as translation_cursor:
                with conn.cursor() as metadata_cursor:
                    import_id = args.import_id or active_import_id(metadata_cursor)
                    metadata_cursor.execute(
                        """
                        select count(*), coalesce(sum(actual_line_count), 0)
                        from db_sections
                        where import_id = %s
                        """,
                        (import_id,),
                    )
                    section_count, data_line_count = metadata_cursor.fetchone()

                translation_iter = translated_fields(
                    translation_cursor,
                    import_id,
                    args.translated_status,
                    args.require_occurrence_status,
                )
                next_translation = next(translation_iter, None)
                replaced_fields = 0
                exported_sections = 0
                exported_data_lines = 0

                line_cursor.execute(
                    """
                    select
                        ds.section_order,
                        ds.name,
                        ds.actual_line_count,
                        dl.line_no,
                        dl.raw_text
                    from db_sections ds
                    left join db_lines dl
                      on dl.section_id = ds.id
                    where ds.import_id = %s
                    order by ds.section_order, dl.line_no nulls first
                    """,
                    (import_id,),
                )

                current_section_order = None
                with output_path.open("w", encoding="utf-8", newline="\n") as output:
                    for (
                        section_order,
                        section_name,
                        section_line_count,
                        line_no,
                        raw_text,
                    ) in line_cursor:
                        if current_section_order != section_order:
                            current_section_order = section_order
                            exported_sections += 1
                            output.write(f"{section_name}|{section_line_count}\n")

                        if line_no is None:
                            continue

                        line_fields = None
                        if (
                            next_translation is not None
                            and next_translation[0] == (section_order, line_no)
                        ):
                            line_fields = next_translation[1]
                            next_translation = next(translation_iter, None)

                        if line_fields:
                            fields = raw_text.split("#")
                            for field_index, translated_text in line_fields.items():
                                if 0 <= field_index < len(fields):
                                    fields[field_index] = translated_text
                                    replaced_fields += 1
                            output.write("#".join(fields))
                        else:
                            output.write(raw_text)
                        output.write("\n")
                        exported_data_lines += 1

    print(f"import_id: {import_id}")
    print(f"output: {output_path}")
    print(f"expected_sections: {section_count}")
    print(f"exported_sections: {exported_sections}")
    print(f"expected_data_lines: {data_line_count}")
    print(f"exported_data_lines: {exported_data_lines}")
    print(f"replaced_fields: {replaced_fields}")
    print(f"size_bytes: {output_path.stat().st_size}")
    if next_translation is not None:
        print("warning: unused translated fields remain after export", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
