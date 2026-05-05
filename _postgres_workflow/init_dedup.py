#!/usr/bin/env python3
"""Initialize deduplicated translation values and occurrences from db_fields."""

from __future__ import annotations

import argparse
import os
import sys
import tomllib
from pathlib import Path

import psycopg2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create translation_values and translation_occurrences from db_fields.",
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL"),
        help="Postgres connection string. Defaults to DATABASE_URL, then .codex/config.toml.",
    )
    parser.add_argument(
        "--context-key",
        default="default",
        help="Context key for deduped translation values.",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Truncate translation tables before initializing.",
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


def main() -> int:
    args = parse_args()
    database_url = args.database_url or load_database_url_from_codex_config()
    if not database_url:
        print(
            "Missing database URL. Set DATABASE_URL or pass --database-url.",
            file=sys.stderr,
        )
        return 2

    with psycopg2.connect(database_url) as conn:
        with conn.cursor() as cursor:
            if args.reset:
                cursor.execute(
                    """
                    truncate table
                        translation_overrides,
                        translation_occurrences,
                        translation_values
                    restart identity cascade
                    """
                )

            cursor.execute(
                """
                insert into translation_values (
                    source_text,
                    source_sha256,
                    context_key,
                    translated_text,
                    status
                )
                select distinct on (df.source_sha256)
                    df.source_text,
                    df.source_sha256,
                    %s,
                    null,
                    'pending'::translation_status
                from db_fields df
                order by df.source_sha256, df.id
                on conflict (source_sha256, context_key) do nothing
                """,
                (args.context_key,),
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
                    'db_field'::occurrence_kind,
                    df.id,
                    null,
                    tv.id,
                    tv.status,
                    %s
                from db_fields df
                join translation_values tv
                  on tv.source_sha256 = df.source_sha256
                 and tv.context_key = %s
                on conflict (db_field_id) do nothing
                """,
                (args.context_key, args.context_key),
            )
            inserted_occurrences = cursor.rowcount

            cursor.execute(
                """
                select
                    (select count(*) from translation_values) as translation_values,
                    (select count(*) from translation_occurrences) as translation_occurrences,
                    (
                        select count(*)
                        from translation_occurrences toc
                        where toc.translation_value_id is null
                    ) as unmapped_occurrences,
                    (
                        select count(distinct source_sha256)
                        from db_fields
                    ) as distinct_sources,
                    (select count(*) from db_fields) as db_fields
                """
            )
            counts = cursor.fetchone()

    print(f"inserted_values: {inserted_values}")
    print(f"inserted_occurrences: {inserted_occurrences}")
    print(f"translation_values: {counts[0]}")
    print(f"translation_occurrences: {counts[1]}")
    print(f"unmapped_occurrences: {counts[2]}")
    print(f"distinct_sources: {counts[3]}")
    print(f"db_fields: {counts[4]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
