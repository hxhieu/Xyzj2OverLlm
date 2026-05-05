#!/usr/bin/env python3
"""Import raw dynamic string contracts into Postgres asset entries."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import sys
import tomllib
from pathlib import Path
from typing import Sequence

import psycopg2


HAN_START = "\u4e00"
HAN_END = "\u9fff"


class CopyBuffer:
    def __init__(
        self,
        cursor,
        table_name: str,
        columns: Sequence[str],
        chunk_size: int = 10_000,
    ) -> None:
        self.cursor = cursor
        self.table_name = table_name
        self.columns = columns
        self.chunk_size = chunk_size
        self.rows: list[Sequence[object | None]] = []
        self.total = 0

    def append(self, row: Sequence[object | None]) -> None:
        self.rows.append(row)
        if len(self.rows) >= self.chunk_size:
            self.flush()

    def flush(self) -> None:
        if not self.rows:
            return

        output = io.StringIO()
        writer = csv.writer(
            output,
            delimiter="\t",
            quotechar='"',
            escapechar='"',
            lineterminator="\n",
        )
        for row in self.rows:
            writer.writerow(["\\N" if value is None else value for value in row])
        output.seek(0)

        column_sql = ", ".join(self.columns)
        sql = (
            f"COPY {self.table_name} ({column_sql}) "
            "FROM STDIN WITH (FORMAT csv, DELIMITER E'\\t', QUOTE '\"', "
            "ESCAPE '\"', NULL '\\N')"
        )
        self.cursor.copy_expert(sql, output)
        self.total += len(self.rows)
        self.rows.clear()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import raw dynamicStrings.txt into Postgres.",
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL"),
        help="Postgres connection string. Defaults to DATABASE_URL, then .codex/config.toml.",
    )
    parser.add_argument(
        "--source",
        default="Files/Raw/DynamicStrings/dynamicStrings.txt",
        help="Raw dynamicStrings.txt path.",
    )
    parser.add_argument(
        "--source-name",
        default="dynamicStrings.txt",
        help="Asset source name stored in Postgres.",
    )
    parser.add_argument(
        "--reset-file",
        action="store_true",
        help="Delete existing dynamic string asset entries for this source first.",
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


def source_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def has_han(text: str) -> bool:
    return any(HAN_START <= char <= HAN_END for char in text)


def parse_dynamic_line(line: str) -> tuple[str, str, int, str, str]:
    parts = line.rstrip("\n").rstrip("\r").split(",", 4)
    if len(parts) != 5:
        raise ValueError(f"Invalid dynamic string row: {line[:160]}")
    type_name, method, il_offset, raw_text, parameters = parts
    return type_name, method, int(il_offset), raw_text, parameters


def iter_rows(path: Path):
    with path.open("r", encoding="utf-8-sig", newline="\n") as handle:
        for entry_no, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            type_name, method, il_offset, raw_text, parameters = parse_dynamic_line(line)
            payload = {
                "type": type_name,
                "method": method,
                "iLOffset": il_offset,
                "parameters": parameters,
                "raw_line": line.rstrip("\n").rstrip("\r"),
            }
            yield (
                entry_no,
                raw_text,
                source_hash(raw_text),
                json.dumps(payload, ensure_ascii=False),
                has_han(raw_text),
            )


def main() -> int:
    args = parse_args()
    database_url = args.database_url or load_database_url_from_codex_config()
    if not database_url:
        print(
            "Missing database URL. Set DATABASE_URL or pass --database-url.",
            file=sys.stderr,
        )
        return 2

    source = Path(args.source)
    if not source.exists():
        print(f"Source not found: {source}", file=sys.stderr)
        return 2

    kind = "dynamic_string"
    with psycopg2.connect(database_url) as conn:
        with conn.cursor() as cursor:
            if args.reset_file:
                cursor.execute(
                    """
                    delete from asset_entries
                    where kind = %s::occurrence_kind
                      and source_name = %s
                    """,
                    (kind, args.source_name),
                )

            cursor.execute(
                """
                create temp table dynamic_import_tmp (
                    entry_no integer not null,
                    source_text text not null,
                    source_sha256 text not null,
                    raw_payload jsonb not null,
                    has_han boolean not null
                ) on commit drop
                """
            )
            buffer = CopyBuffer(
                cursor,
                "dynamic_import_tmp",
                ("entry_no", "source_text", "source_sha256", "raw_payload", "has_han"),
            )
            for row in iter_rows(source):
                buffer.append(row)
            buffer.flush()

            cursor.execute("select count(*), count(*) filter (where has_han) from dynamic_import_tmp")
            imported_rows, han_rows = cursor.fetchone()

            cursor.execute(
                """
                insert into asset_entries (
                    kind,
                    source_name,
                    entry_no,
                    source_text,
                    source_sha256,
                    raw_payload,
                    has_han
                )
                select
                    %s::occurrence_kind,
                    %s,
                    entry_no,
                    source_text,
                    source_sha256,
                    raw_payload,
                    has_han
                from dynamic_import_tmp
                on conflict (kind, source_name, entry_no) do update
                set source_text = excluded.source_text,
                    source_sha256 = excluded.source_sha256,
                    raw_payload = excluded.raw_payload,
                    has_han = excluded.has_han
                """,
                (kind, args.source_name),
            )
            upserted_assets = cursor.rowcount

            cursor.execute(
                """
                insert into translation_values (
                    source_text,
                    source_sha256,
                    context_key,
                    translated_text,
                    status
                )
                select distinct on (ae.source_sha256)
                    ae.source_text,
                    ae.source_sha256,
                    'default',
                    null,
                    'pending'::translation_status
                from asset_entries ae
                where ae.kind = %s::occurrence_kind
                  and ae.source_name = %s
                  and ae.has_han
                order by ae.source_sha256, ae.id
                on conflict (source_sha256, context_key) do nothing
                """,
                (kind, args.source_name),
            )
            inserted_values = cursor.rowcount

            cursor.execute(
                """
                insert into translation_occurrences (
                    kind,
                    db_field_id,
                    asset_entry_id,
                    translation_value_id,
                    status,
                    context_key
                )
                select
                    %s::occurrence_kind,
                    null,
                    ae.id,
                    tv.id,
                    tv.status,
                    'default'
                from asset_entries ae
                join translation_values tv
                  on tv.source_sha256 = ae.source_sha256
                 and tv.context_key = 'default'
                where ae.kind = %s::occurrence_kind
                  and ae.source_name = %s
                  and ae.has_han
                on conflict (asset_entry_id) do update
                set translation_value_id = excluded.translation_value_id,
                    status = excluded.status,
                    context_key = excluded.context_key
                """
                ,
                (kind, kind, args.source_name),
            )
            upserted_occurrences = cursor.rowcount

            cursor.execute(
                """
                select
                    (select count(*) from asset_entries where kind = %s::occurrence_kind and source_name = %s) as assets,
                    (
                        select count(*)
                        from translation_occurrences toc
                        join asset_entries ae on ae.id = toc.asset_entry_id
                        where ae.kind = %s::occurrence_kind
                          and ae.source_name = %s
                    ) as occurrences
                """,
                (kind, args.source_name, kind, args.source_name),
            )
            final_assets, final_occurrences = cursor.fetchone()

    print(f"source: {source}")
    print(f"source_name: {args.source_name}")
    print(f"kind: {kind}")
    print(f"imported_rows: {imported_rows}")
    print(f"han_rows: {han_rows}")
    print(f"upserted_assets: {upserted_assets}")
    print(f"inserted_values: {inserted_values}")
    print(f"upserted_occurrences: {upserted_occurrences}")
    print(f"final_assets: {final_assets}")
    print(f"final_occurrences: {final_occurrences}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
