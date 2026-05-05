#!/usr/bin/env python3
"""Import legacy non-db text assets from SQLite into Postgres asset entries."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import sqlite3
import sys
import tomllib
from pathlib import Path
from typing import Sequence

import psycopg2


ASSET_FILES = {
    "dumpedPrefabText.txt": "prefab_text",
    "dynamicStrings.txt": "dynamic_string",
}


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
        description="Import non-db asset translations from legacy SQLite.",
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL"),
        help="Postgres connection string. Defaults to DATABASE_URL, then .codex/config.toml.",
    )
    parser.add_argument(
        "--sqlite-db",
        default="_viethoa/glossary-audit.db",
        help="Legacy SQLite audit DB path.",
    )
    parser.add_argument(
        "--file",
        choices=sorted(ASSET_FILES),
        default="dumpedPrefabText.txt",
        help="Asset file to import from SQLite.",
    )
    parser.add_argument(
        "--reset-file",
        action="store_true",
        help="Delete existing asset entries and their occurrences for this file first.",
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


def sqlite_rows(sqlite_db: Path, source_file: str):
    query = """
        select
            line_index + 1 as entry_no,
            split_index,
            source_text,
            translated,
            status,
            safe_to_translate,
            flagged_for_retranslation
        from converted_file_splits
        where source_file = ?
        order by line_index, split_order
    """
    with sqlite3.connect(sqlite_db) as conn:
        conn.row_factory = sqlite3.Row
        for row in conn.execute(query, (source_file,)):
            payload = {
                "split_index": row["split_index"],
                "sqlite_status": row["status"],
                "safe_to_translate": bool(row["safe_to_translate"]),
                "flagged_for_retranslation": bool(row["flagged_for_retranslation"]),
                "legacy_translated": row["translated"],
            }
            yield (
                row["entry_no"],
                row["source_text"],
                source_hash(row["source_text"]),
                json.dumps(payload, ensure_ascii=False),
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

    sqlite_db = Path(args.sqlite_db)
    if not sqlite_db.exists():
        print(f"SQLite DB not found: {sqlite_db}", file=sys.stderr)
        return 2

    kind = ASSET_FILES[args.file]
    with psycopg2.connect(database_url) as conn:
        with conn.cursor() as cursor:
            if args.reset_file:
                cursor.execute(
                    """
                    delete from asset_entries
                    where kind = %s::occurrence_kind
                      and source_name = %s
                    """,
                    (kind, args.file),
                )

            cursor.execute(
                """
                create temp table asset_import_tmp (
                    entry_no integer not null,
                    source_text text not null,
                    source_sha256 text not null,
                    raw_payload jsonb not null
                ) on commit drop
                """
            )
            buffer = CopyBuffer(
                cursor,
                "asset_import_tmp",
                ("entry_no", "source_text", "source_sha256", "raw_payload"),
            )
            for row in sqlite_rows(sqlite_db, args.file):
                buffer.append(row)
            buffer.flush()

            cursor.execute("select count(*) from asset_import_tmp")
            imported_rows = cursor.fetchone()[0]

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
                    source_text ~ '[一-鿿]'
                from asset_import_tmp
                on conflict (kind, source_name, entry_no) do update
                set source_text = excluded.source_text,
                    source_sha256 = excluded.source_sha256,
                    raw_payload = excluded.raw_payload,
                    has_han = excluded.has_han
                """,
                (kind, args.file),
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
                (kind, args.file),
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
                """,
                (kind, kind, args.file),
            )
            upserted_occurrences = cursor.rowcount

            cursor.execute(
                """
                create temp table asset_backfill_tmp as
                select
                    toc.id as occurrence_id,
                    tv.id as translation_value_id,
                    ae.raw_payload->>'legacy_translated' as translated_text,
                    (ae.raw_payload->>'sqlite_status')::translation_status as sqlite_status
                from asset_entries ae
                join translation_occurrences toc
                  on toc.asset_entry_id = ae.id
                join translation_values tv
                  on tv.id = toc.translation_value_id
                where ae.kind = %s::occurrence_kind
                  and ae.source_name = %s
                  and ae.raw_payload->>'sqlite_status' in ('locked', 'reviewed')
                  and coalesce(ae.raw_payload->>'legacy_translated', '') <> ''
                  and ae.raw_payload->>'legacy_translated' <> ae.source_text
                """,
                (kind, args.file),
            )
            cursor.execute("create index on asset_backfill_tmp(translation_value_id)")
            cursor.execute("select count(*) from asset_backfill_tmp")
            backfill_candidates = cursor.fetchone()[0]

            cursor.execute(
                """
                create temp table asset_defaults_tmp as
                with grouped as (
                    select
                        translation_value_id,
                        translated_text,
                        max(case sqlite_status when 'locked' then 2 when 'reviewed' then 1 else 0 end) as best_rank,
                        count(*) as use_count
                    from asset_backfill_tmp
                    group by translation_value_id, translated_text
                ),
                ranked as (
                    select
                        *,
                        row_number() over (
                            partition by translation_value_id
                            order by use_count desc, best_rank desc, translated_text
                        ) as rn
                    from grouped
                )
                select
                    translation_value_id,
                    translated_text,
                    case best_rank
                        when 2 then 'locked'::translation_status
                        when 1 then 'reviewed'::translation_status
                        else 'pending'::translation_status
                    end as status
                from ranked
                where rn = 1
                """
            )
            cursor.execute("create index on asset_defaults_tmp(translation_value_id)")

            cursor.execute(
                """
                update translation_values tv
                set translated_text = adt.translated_text,
                    status = adt.status
                from asset_defaults_tmp adt
                where tv.id = adt.translation_value_id
                  and (
                      tv.translated_text is null
                      or tv.status <> 'locked'::translation_status
                  )
                """
            )
            updated_values = cursor.rowcount

            cursor.execute(
                """
                update translation_occurrences toc
                set status = abt.sqlite_status
                from asset_backfill_tmp abt
                where toc.id = abt.occurrence_id
                """
            )
            updated_occurrences = cursor.rowcount

            cursor.execute(
                """
                insert into translation_overrides (
                    occurrence_id,
                    translated_text,
                    status,
                    reason
                )
                select
                    abt.occurrence_id,
                    abt.translated_text,
                    abt.sqlite_status,
                    'legacy sqlite asset backfill conflict'
                from asset_backfill_tmp abt
                join asset_defaults_tmp adt
                  on adt.translation_value_id = abt.translation_value_id
                where abt.translated_text <> adt.translated_text
                on conflict (occurrence_id) do update
                set translated_text = excluded.translated_text,
                    status = excluded.status,
                    reason = excluded.reason
                """
            )
            upserted_overrides = cursor.rowcount

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
                (kind, args.file, kind, args.file),
            )
            final_assets, final_occurrences = cursor.fetchone()

    print(f"source_file: {args.file}")
    print(f"kind: {kind}")
    print(f"imported_rows: {imported_rows}")
    print(f"upserted_assets: {upserted_assets}")
    print(f"inserted_values: {inserted_values}")
    print(f"upserted_occurrences: {upserted_occurrences}")
    print(f"backfill_candidates: {backfill_candidates}")
    print(f"updated_values: {updated_values}")
    print(f"updated_occurrences: {updated_occurrences}")
    print(f"upserted_overrides: {upserted_overrides}")
    print(f"final_assets: {final_assets}")
    print(f"final_occurrences: {final_occurrences}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
