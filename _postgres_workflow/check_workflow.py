#!/usr/bin/env python3
"""Run read-only QA checks for the Postgres translation workflow."""

from __future__ import annotations

import argparse
import os
import sys
import tomllib
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor


CHECKS = [
    (
        "active_import_exists",
        """
        select count(*) = 1 as ok
        from workflow_metadata
        where key = 'active_db_import'
          and value ? 'import_id'
        """,
    ),
    (
        "section_line_counts_match",
        """
        select count(*) = 0 as ok
        from db_sections
        where declared_line_count <> actual_line_count
        """,
    ),
    (
        "no_orphan_lines",
        """
        select count(*) = 0 as ok
        from db_lines dl
        left join db_sections ds on ds.id = dl.section_id
        where ds.id is null
        """,
    ),
    (
        "no_orphan_fields",
        """
        select count(*) = 0 as ok
        from db_fields df
        left join db_lines dl on dl.id = df.line_id
        where dl.id is null
        """,
    ),
    (
        "all_db_fields_have_occurrence",
        """
        select count(*) = 0 as ok
        from db_fields df
        left join translation_occurrences toc on toc.db_field_id = df.id
        where toc.id is null
        """,
    ),
    (
        "all_han_assets_have_occurrence",
        """
        select count(*) = 0 as ok
        from asset_entries ae
        left join translation_occurrences toc on toc.asset_entry_id = ae.id
        where ae.has_han and toc.id is null
        """,
    ),
    (
        "no_unmapped_occurrences",
        """
        select count(*) = 0 as ok
        from translation_occurrences
        where translation_value_id is null
        """,
    ),
    (
        "db_hash_maps_match",
        """
        select count(*) = 0 as ok
        from translation_occurrences toc
        join db_fields df on df.id = toc.db_field_id
        join translation_values tv on tv.id = toc.translation_value_id
        where toc.kind = 'db_field'::occurrence_kind
          and df.source_sha256 <> tv.source_sha256
        """,
    ),
    (
        "asset_hash_maps_match",
        """
        select count(*) = 0 as ok
        from translation_occurrences toc
        join asset_entries ae on ae.id = toc.asset_entry_id
        join translation_values tv on tv.id = toc.translation_value_id
        where toc.kind in ('prefab_text', 'dynamic_string')
          and ae.source_sha256 <> tv.source_sha256
        """,
    ),
    (
        "no_duplicate_translation_values",
        """
        select count(*) = 0 as ok
        from (
            select source_sha256, context_key, count(*)
            from translation_values
            group by source_sha256, context_key
            having count(*) > 1
        ) d
        """,
    ),
    (
        "no_bad_kind_links",
        """
        select count(*) = 0 as ok
        from translation_occurrences toc
        left join db_fields df on df.id = toc.db_field_id
        left join asset_entries ae on ae.id = toc.asset_entry_id
        where (toc.kind = 'db_field'::occurrence_kind and df.id is null)
           or (toc.kind in ('prefab_text','dynamic_string') and ae.id is null)
           or (toc.kind <> 'db_field'::occurrence_kind and ae.kind <> toc.kind)
        """,
    ),
    (
        "no_unused_translation_values",
        """
        select count(*) = 0 as ok
        from translation_values tv
        left join translation_occurrences toc on toc.translation_value_id = tv.id
        where toc.id is null
        """,
    ),
    (
        "overrides_linked",
        """
        select count(*) = 0 as ok
        from translation_overrides tov
        left join translation_occurrences toc on toc.id = tov.occurrence_id
        where toc.id is null
        """,
    ),
    (
        "overrides_not_same_as_default",
        """
        select count(*) = 0 as ok
        from translation_overrides tov
        join translation_occurrences toc on toc.id = tov.occurrence_id
        join translation_values tv on tv.id = toc.translation_value_id
        where tov.translated_text = tv.translated_text
        """,
    ),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run read-only QA checks for the Postgres workflow.",
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL"),
        help="Postgres connection string. Defaults to DATABASE_URL, then .codex/config.toml.",
    )
    parser.add_argument(
        "--section-limit",
        type=int,
        default=80,
        help="Maximum section breakdown rows to print, ordered by pending count.",
    )
    parser.add_argument(
        "--format",
        choices=["text", "markdown"],
        default="text",
        help="Output format.",
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


def scalar(cursor, sql: str):
    cursor.execute(sql)
    return cursor.fetchone()[0]


def print_rows(title: str, rows) -> None:
    print(f"\n{title}:")
    for row in rows:
        print("  " + ", ".join(f"{key}={value}" for key, value in row.items()))


def md_escape(value) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", "<br>")


def print_markdown_table(title: str, rows) -> None:
    print(f"\n## {title}\n")
    rows = list(rows)
    if not rows:
        print("_No rows._")
        return
    headers = list(rows[0].keys())
    print("| " + " | ".join(headers) + " |")
    print("| " + " | ".join("---" for _ in headers) + " |")
    for row in rows:
        print("| " + " | ".join(md_escape(row[key]) for key in headers) + " |")


def print_markdown_checklist(results: list[tuple[str, bool]]) -> None:
    print("# Postgres Translation Workflow Check\n")
    print("## Checks\n")
    for name, ok in results:
        marker = "x" if ok else " "
        status = "OK" if ok else "FAIL"
        print(f"- [{marker}] `{name}`: {status}")


def main() -> int:
    args = parse_args()
    database_url = args.database_url or load_database_url_from_codex_config()
    if not database_url:
        print(
            "Missing database URL. Set DATABASE_URL or pass --database-url.",
            file=sys.stderr,
        )
        return 2

    failed = []
    with psycopg2.connect(database_url) as conn:
        conn.set_session(readonly=True, autocommit=True)
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            check_results = []
            for name, sql in CHECKS:
                cursor.execute(sql)
                ok = bool(cursor.fetchone()["ok"])
                check_results.append((name, ok))
                if not ok:
                    failed.append(name)

            if args.format == "markdown":
                print_markdown_checklist(check_results)
            else:
                print("checks:")
                for name, ok in check_results:
                    print(f"  {'OK' if ok else 'FAIL'} {name}")

            cursor.execute(
                """
                select
                    pg_size_pretty(pg_database_size(current_database())) as database_size,
                    (select count(*) from db_sections) as sections,
                    (select count(*) from db_lines) as lines,
                    (select count(*) from db_fields) as db_fields,
                    (select count(*) from asset_entries) as asset_entries,
                    (select count(*) from translation_values) as translation_values,
                    (select count(*) from translation_occurrences) as translation_occurrences,
                    (select count(*) from translation_overrides) as translation_overrides
                """
            )
            rows = cursor.fetchall()
            if args.format == "markdown":
                print_markdown_table("Summary", rows)
            else:
                print_rows("summary", rows)

            cursor.execute(
                """
                select status, count(*) as count
                from translation_values
                group by status
                order by status
                """
            )
            rows = cursor.fetchall()
            if args.format == "markdown":
                print_markdown_table("Translation Values By Status", rows)
            else:
                print_rows("translation_values_by_status", rows)

            cursor.execute(
                """
                select kind, status, count(*) as count
                from translation_occurrences
                group by kind, status
                order by kind, status
                """
            )
            rows = cursor.fetchall()
            if args.format == "markdown":
                print_markdown_table("Occurrences By Kind And Status", rows)
            else:
                print_rows("occurrences_by_kind_status", rows)

            cursor.execute(
                """
                select kind, source_name, count(*) as count,
                       count(*) filter (where has_han) as han_count
                from asset_entries
                group by kind, source_name
                order by kind, source_name
                """
            )
            rows = cursor.fetchall()
            if args.format == "markdown":
                print_markdown_table("Assets", rows)
            else:
                print_rows("assets", rows)

            cursor.execute(
                """
                select toc.kind, count(*) as count
                from translation_occurrences toc
                join translation_values tv on tv.id = toc.translation_value_id
                where toc.status = 'pending'::translation_status
                  and tv.status = 'locked'::translation_status
                group by toc.kind
                order by toc.kind
                """
            )
            rows = cursor.fetchall()
            if args.format == "markdown":
                print_markdown_table("Pending Occurrences With Locked Values", rows)
            else:
                print_rows("pending_occurrences_with_locked_values", rows)

            cursor.execute(
                """
                with per_section as (
                    select
                        ds.name as section,
                        count(*) as occurrences,
                        count(distinct tv.id) as distinct_values,
                        count(*) filter (
                            where toc.status = 'locked'::translation_status
                        ) as locked_occurrences,
                        count(*) filter (
                            where toc.status = 'reviewed'::translation_status
                        ) as reviewed_occurrences,
                        count(*) filter (
                            where toc.status = 'pending'::translation_status
                        ) as pending_occurrences,
                        count(distinct tv.id) filter (
                            where tv.status = 'locked'::translation_status
                        ) as locked_values,
                        count(distinct tv.id) filter (
                            where tv.status = 'reviewed'::translation_status
                        ) as reviewed_values,
                        count(distinct tv.id) filter (
                            where tv.status = 'pending'::translation_status
                        ) as pending_values,
                        count(*) filter (
                            where toc.status = 'pending'::translation_status
                              and tv.status = 'locked'::translation_status
                        ) as pending_occurrences_with_locked_value
                    from translation_occurrences toc
                    join db_fields df on df.id = toc.db_field_id
                    join db_lines dl on dl.id = df.line_id
                    join db_sections ds on ds.id = dl.section_id
                    join translation_values tv on tv.id = toc.translation_value_id
                    where toc.kind = 'db_field'::occurrence_kind
                    group by ds.name
                )
                select
                    section,
                    occurrences,
                    distinct_values,
                    locked_occurrences,
                    reviewed_occurrences,
                    pending_occurrences,
                    locked_values,
                    reviewed_values,
                    pending_values,
                    pending_occurrences_with_locked_value
                from per_section
                order by pending_values desc, pending_occurrences desc, section
                limit %s
                """,
                (args.section_limit,),
            )
            rows = cursor.fetchall()
            if args.format == "markdown":
                print_markdown_table("Sections By Pending Occurrences", rows)
            else:
                print_rows("sections_by_pending_occurrences", rows)

            cursor.execute(
                """
                select
                    ae.kind,
                    ae.source_name,
                    count(*) as occurrences,
                    count(distinct tv.id) as distinct_values,
                    count(*) filter (
                        where toc.status = 'locked'::translation_status
                    ) as locked_occurrences,
                    count(*) filter (
                        where toc.status = 'reviewed'::translation_status
                    ) as reviewed_occurrences,
                    count(*) filter (
                        where toc.status = 'pending'::translation_status
                    ) as pending_occurrences,
                    count(distinct tv.id) filter (
                        where tv.status = 'locked'::translation_status
                    ) as locked_values,
                    count(distinct tv.id) filter (
                        where tv.status = 'reviewed'::translation_status
                    ) as reviewed_values,
                    count(distinct tv.id) filter (
                        where tv.status = 'pending'::translation_status
                    ) as pending_values
                from translation_occurrences toc
                join asset_entries ae on ae.id = toc.asset_entry_id
                join translation_values tv on tv.id = toc.translation_value_id
                where toc.kind in ('prefab_text', 'dynamic_string')
                group by ae.kind, ae.source_name
                order by ae.kind, ae.source_name
                """
            )
            rows = cursor.fetchall()
            if args.format == "markdown":
                print_markdown_table("Asset Breakdown", rows)
            else:
                print_rows("asset_breakdown", rows)

    if failed:
        if args.format == "markdown":
            print("\n## Result\n\n`failed_checks`: " + ", ".join(failed))
        else:
            print("\nfailed_checks: " + ", ".join(failed), file=sys.stderr)
        return 1
    if args.format == "markdown":
        print("\n## Result\n\n`all_checks_passed`: `true`")
    else:
        print("\nall_checks_passed: true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
