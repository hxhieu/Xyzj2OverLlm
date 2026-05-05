#!/usr/bin/env python3
"""Backfill existing translated DB fields from the legacy SQLite audit DB."""

from __future__ import annotations

import argparse
import csv
import io
import os
import sqlite3
import sys
import tomllib
from pathlib import Path
from typing import Sequence

import psycopg2


COMPLETED_STATUSES = ("locked", "reviewed")


class CopyBuffer:
    def __init__(
        self,
        cursor,
        table_name: str,
        columns: Sequence[str],
        chunk_size: int = 20_000,
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
        description="Backfill Postgres translations from legacy SQLite converted_file_splits.",
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
        "--reset-existing",
        action="store_true",
        help="Clear existing translated_text/status/overrides before backfill.",
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


def sqlite_rows(sqlite_db: Path):
    query = """
        select
            source_file,
            replace(source_file, '.txt', '') as section_name,
            line_index + 1 as line_no,
            split_index as field_index,
            source_text,
            translated,
            status
        from converted_file_splits
        where status in ('locked', 'reviewed')
          and safe_to_translate = 1
          and flagged_for_retranslation = 0
          and translated <> ''
          and translated <> source_text
        order by source_file, line_index, split_index
    """
    with sqlite3.connect(sqlite_db) as conn:
        conn.row_factory = sqlite3.Row
        for row in conn.execute(query):
            yield (
                row["source_file"],
                row["section_name"],
                row["line_no"],
                row["field_index"],
                row["source_text"],
                row["translated"],
                row["status"],
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

    with psycopg2.connect(database_url) as conn:
        with conn.cursor() as cursor:
            if args.reset_existing:
                cursor.execute("truncate table translation_overrides restart identity")
                cursor.execute(
                    """
                    update translation_values
                    set translated_text = null,
                        status = 'pending'::translation_status
                    """
                )
                cursor.execute(
                    """
                    update translation_occurrences
                    set status = 'pending'::translation_status
                    """
                )

            cursor.execute(
                """
                create temp table sqlite_backfill_tmp (
                    source_file text not null,
                    section_name text not null,
                    line_no integer not null,
                    field_index integer not null,
                    source_text text not null,
                    translated_text text not null,
                    sqlite_status translation_status not null
                ) on commit drop
                """
            )
            buffer = CopyBuffer(
                cursor,
                "sqlite_backfill_tmp",
                (
                    "source_file",
                    "section_name",
                    "line_no",
                    "field_index",
                    "source_text",
                    "translated_text",
                    "sqlite_status",
                ),
            )
            for row in sqlite_rows(sqlite_db):
                buffer.append(row)
            buffer.flush()

            cursor.execute("select count(*) from sqlite_backfill_tmp")
            sqlite_candidates = cursor.fetchone()[0]

            cursor.execute(
                """
                create temp table backfill_matches_tmp as
                select
                    toc.id as occurrence_id,
                    tv.id as translation_value_id,
                    sbt.translated_text,
                    sbt.sqlite_status,
                    case sbt.sqlite_status
                        when 'locked' then 2
                        when 'reviewed' then 1
                        else 0
                    end as status_rank
                from sqlite_backfill_tmp sbt
                join db_sections ds
                  on ds.name = sbt.section_name
                join db_lines dl
                  on dl.section_id = ds.id
                 and dl.line_no = sbt.line_no
                join db_fields df
                  on df.line_id = dl.id
                 and df.field_index = sbt.field_index
                 and df.source_text = sbt.source_text
                join translation_occurrences toc
                  on toc.db_field_id = df.id
                join translation_values tv
                  on tv.id = toc.translation_value_id
                """
            )
            cursor.execute("create index on backfill_matches_tmp(translation_value_id)")
            cursor.execute("create index on backfill_matches_tmp(occurrence_id)")
            cursor.execute("select count(*) from backfill_matches_tmp")
            matched_candidates = cursor.fetchone()[0]

            cursor.execute(
                """
                create temp table backfill_defaults_tmp as
                with grouped as (
                    select
                        translation_value_id,
                        translated_text,
                        max(status_rank) as best_rank,
                        count(*) as use_count
                    from backfill_matches_tmp
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
            cursor.execute("create index on backfill_defaults_tmp(translation_value_id)")

            cursor.execute(
                """
                update translation_values tv
                set translated_text = bdt.translated_text,
                    status = bdt.status
                from backfill_defaults_tmp bdt
                where tv.id = bdt.translation_value_id
                """
            )
            updated_values = cursor.rowcount

            cursor.execute(
                """
                update translation_occurrences toc
                set status = bmt.sqlite_status
                from backfill_matches_tmp bmt
                where toc.id = bmt.occurrence_id
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
                    bmt.occurrence_id,
                    bmt.translated_text,
                    bmt.sqlite_status,
                    'legacy sqlite backfill conflict'
                from backfill_matches_tmp bmt
                join backfill_defaults_tmp bdt
                  on bdt.translation_value_id = bmt.translation_value_id
                where bmt.translated_text <> bdt.translated_text
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
                    (select count(*) from translation_values where translated_text is not null) as translated_values,
                    (select count(*) from translation_overrides) as overrides
                """
            )
            translated_values, overrides = cursor.fetchone()

            cursor.execute(
                """
                select count(*)
                from sqlite_backfill_tmp sbt
                where not exists (
                    select 1
                    from db_sections ds
                    join db_lines dl
                      on dl.section_id = ds.id
                     and dl.line_no = sbt.line_no
                    join db_fields df
                      on df.line_id = dl.id
                     and df.field_index = sbt.field_index
                     and df.source_text = sbt.source_text
                    where ds.name = sbt.section_name
                )
                """
            )
            unmatched_candidates = cursor.fetchone()[0]

    print(f"sqlite_candidates: {sqlite_candidates}")
    print(f"matched_candidates: {matched_candidates}")
    print(f"unmatched_candidates: {unmatched_candidates}")
    print(f"updated_values: {updated_values}")
    print(f"updated_occurrences: {updated_occurrences}")
    print(f"upserted_overrides: {upserted_overrides}")
    print(f"translated_values: {translated_values}")
    print(f"overrides: {overrides}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
