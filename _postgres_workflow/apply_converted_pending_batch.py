#!/usr/bin/env python3
"""Apply one reviewed Vietnamese batch from Files/Converted to pending DB values.

This is intentionally conservative:
- DB fields only.
- Excludes dialogue-like sections.
- Requires Vietnamese diacritics in the converted translation, so English
  placeholders are not promoted to reviewed.
- Skips dedup values with conflicting converted translations.
"""

from __future__ import annotations

import argparse
import csv
import io
import os
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

import psycopg2
import yaml


DIALOGUE_SECTIONS = {
    "ai_dialog",
    "npc_interact",
    "npc_interact_dangmojianghu",
}

VIETNAMESE_DIACRITIC_RE = r"[ÀÁÂÃÈÉÊÌÍÒÓÔÕÙÚÝàáâãèéêìíòóôõùúýĂăĐđĨĩŨũƠơƯưẠ-ỹ]"
HAN_RE = r"[一-鿿]"


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
        description="Apply one non-dialogue pending batch from Files/Converted.",
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL"),
        help="Postgres connection string. Defaults to DATABASE_URL, then .codex/config.toml.",
    )
    parser.add_argument(
        "--converted-dir",
        default="Files/Converted",
        help="Converted YAML directory.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=1000,
        help="Maximum translation_values to update.",
    )
    parser.add_argument(
        "--report",
        default="_working/translation_batches.md",
        help="Markdown report path to append.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write updates. Without this, dry-run only.",
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


def converted_rows(converted_dir: Path, sections: set[str]):
    for section in sorted(sections):
        if section in DIALOGUE_SECTIONS:
            continue
        path = converted_dir / f"{section}.txt"
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8-sig") as handle:
            rows = yaml.safe_load(handle) or []
        for line_no, row in enumerate(rows, start=1):
            for split in row.get("splits") or []:
                source_text = split.get("text")
                translated_text = split.get("translated")
                field_index = split.get("split")
                if (
                    source_text is None
                    or translated_text is None
                    or field_index is None
                    or translated_text == ""
                    or translated_text == source_text
                ):
                    continue
                yield section, line_no, int(field_index), source_text, translated_text


def append_report(path: Path, report: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existed = path.exists()
    with path.open("a", encoding="utf-8") as handle:
        if not existed:
            handle.write("# Translation Batches\n\n")
        handle.write(f"## {report['timestamp']}\n")
        handle.write(f"- Mode: {report['mode']}\n")
        handle.write(f"- Attempted: {report['attempted']}\n")
        handle.write(f"- Updated: {report['updated']}\n")
        handle.write(f"- Candidate values: {report['candidate_values']}\n")
        handle.write(f"- Conflicts skipped: {report['conflict_values']}\n")
        handle.write(f"- Reviewed leftover Han: {report['reviewed_leftover_han']}\n")
        handle.write(f"- Pending remaining DB non-dialogue: {report['pending_remaining']}\n")
        handle.write(f"- Sections touched: {report['sections_touched']}\n")
        handle.write(f"- Notes: {report['notes']}\n\n")


def main() -> int:
    args = parse_args()
    database_url = args.database_url or load_database_url_from_codex_config()
    if not database_url:
        print("Missing database URL. Set DATABASE_URL or pass --database-url.", file=sys.stderr)
        return 2

    converted_dir = Path(args.converted_dir)
    if not converted_dir.exists():
        print(f"Converted directory not found: {converted_dir}", file=sys.stderr)
        return 2

    with psycopg2.connect(database_url) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                create temp table converted_batch_tmp (
                    section_name text not null,
                    line_no integer not null,
                    field_index integer not null,
                    source_text text not null,
                    translated_text text not null
                ) on commit preserve rows
                """
            )

            cursor.execute(
                """
                select distinct ds.name
                from translation_values tv
                join translation_occurrences toc on toc.translation_value_id = tv.id
                join db_fields df on df.id = toc.db_field_id
                join db_lines dl on dl.id = df.line_id
                join db_sections ds on ds.id = dl.section_id
                where tv.status = 'pending'::translation_status
                order by ds.name
                """
            )
            pending_sections = {row[0] for row in cursor.fetchall()}

            buffer = CopyBuffer(
                cursor,
                "converted_batch_tmp",
                ("section_name", "line_no", "field_index", "source_text", "translated_text"),
            )
            for row in converted_rows(converted_dir, pending_sections):
                buffer.append(row)
            buffer.flush()

            cursor.execute(
                "create index on converted_batch_tmp(section_name, line_no, field_index)"
            )

            cursor.execute(
                """
                create temp table converted_matches_tmp on commit preserve rows as
                select
                    tv.id as translation_value_id,
                    cbt.translated_text,
                    ds.name as section_name,
                    dl.line_no
                from converted_batch_tmp cbt
                join db_sections ds on ds.name = cbt.section_name
                join db_lines dl on dl.section_id = ds.id
                 and dl.line_no = cbt.line_no
                join db_fields df on df.line_id = dl.id
                 and df.field_index = cbt.field_index
                 and df.source_text = cbt.source_text
                join translation_occurrences toc on toc.db_field_id = df.id
                join translation_values tv on tv.id = toc.translation_value_id
                where tv.status = 'pending'::translation_status
                  and ds.name <> all(%s)
                  and cbt.translated_text ~ %s
                  and cbt.translated_text !~ %s
                """,
                (list(DIALOGUE_SECTIONS), VIETNAMESE_DIACRITIC_RE, HAN_RE),
            )
            cursor.execute("create index on converted_matches_tmp(translation_value_id)")

            cursor.execute(
                """
                create temp table converted_conflicts_tmp on commit preserve rows as
                select translation_value_id
                from converted_matches_tmp
                group by translation_value_id
                having count(distinct translated_text) > 1
                """
            )

            cursor.execute(
                """
                create temp table batch_updates_tmp on commit preserve rows as
                with stable as (
                    select
                        cm.translation_value_id,
                        min(cm.translated_text) as translated_text,
                        min(cm.section_name) as section_name,
                        min(cm.line_no) as line_no
                    from converted_matches_tmp cm
                    where not exists (
                        select 1
                        from converted_conflicts_tmp cc
                        where cc.translation_value_id = cm.translation_value_id
                    )
                    group by cm.translation_value_id
                )
                select *
                from stable
                order by section_name, line_no, translation_value_id
                limit %s
                """,
                (args.limit,),
            )

            cursor.execute("select count(*) from converted_matches_tmp")
            candidate_matches = cursor.fetchone()[0]
            cursor.execute("select count(distinct translation_value_id) from converted_matches_tmp")
            candidate_values = cursor.fetchone()[0]
            cursor.execute("select count(*) from converted_conflicts_tmp")
            conflict_values = cursor.fetchone()[0]
            cursor.execute("select count(*) from batch_updates_tmp")
            attempted = cursor.fetchone()[0]
            cursor.execute(
                """
                select coalesce(string_agg(section_name || ':' || values, ', ' order by section_name), '')
                from (
                    select section_name, count(*) as values
                    from batch_updates_tmp
                    group by section_name
                ) s
                """
            )
            sections_touched = cursor.fetchone()[0]

            updated = 0
            if args.apply and attempted:
                cursor.execute(
                    """
                    update translation_values tv
                    set translated_text = but.translated_text,
                        status = 'reviewed'::translation_status
                    from batch_updates_tmp but
                    where tv.id = but.translation_value_id
                      and tv.status = 'pending'::translation_status
                    """
                )
                updated = cursor.rowcount
                cursor.execute(
                    """
                    select count(*)
                    from translation_values tv
                    join batch_updates_tmp but on but.translation_value_id = tv.id
                    where tv.status = 'reviewed'::translation_status
                      and tv.translated_text ~ %s
                    """,
                    (HAN_RE,),
                )
                reviewed_leftover_han = cursor.fetchone()[0]
                conn.commit()
            else:
                cursor.execute(
                    """
                    select count(*)
                    from batch_updates_tmp
                    where translated_text ~ %s
                    """,
                    (HAN_RE,),
                )
                reviewed_leftover_han = cursor.fetchone()[0]
                conn.rollback()

    # Use a fresh connection for post-commit QA.
    with psycopg2.connect(database_url) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                select count(distinct tv.id)
                from translation_values tv
                join translation_occurrences toc on toc.translation_value_id = tv.id
                join db_fields df on df.id = toc.db_field_id
                join db_lines dl on dl.id = df.line_id
                join db_sections ds on ds.id = dl.section_id
                where tv.status = 'pending'::translation_status
                  and ds.name <> all(%s)
                """,
                (list(DIALOGUE_SECTIONS),),
            )
            pending_remaining = cursor.fetchone()[0]
    report = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ"),
        "mode": "apply" if args.apply else "dry-run",
        "attempted": attempted,
        "updated": updated,
        "candidate_values": candidate_values,
        "conflict_values": conflict_values,
        "reviewed_leftover_han": reviewed_leftover_han,
        "pending_remaining": pending_remaining,
        "sections_touched": sections_touched,
        "notes": f"candidate_matches={candidate_matches}; dialogue_sections_excluded={sorted(DIALOGUE_SECTIONS)}",
    }
    append_report(Path(args.report), report)

    for key, value in report.items():
        print(f"{key}: {value}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
