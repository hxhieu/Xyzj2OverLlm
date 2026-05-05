#!/usr/bin/env python3
"""Fill pending translations with placeholder text from mod/converted files.

This is a searchability helper: it updates only translation_values.translated_text
for pending values, and deliberately leaves status as pending.
"""

from __future__ import annotations

import argparse
import csv
import io
import os
import re
import sys
import tomllib
from pathlib import Path
from typing import Sequence

import psycopg2
import yaml


HEADER_RE = re.compile(r"^([^#|]+)\|([0-9]+)$")
ASSET_FILES = {
    "dumpedPrefabText.txt": "prefab_text",
    "dynamicStrings.txt": "dynamic_string",
}
UNSAFE_DYNAMIC_TYPES = {
    "LegionZoneGameSDK",
}


class CopyBuffer:
    def __init__(
        self,
        cursor,
        table_name: str,
        columns: Sequence[str],
        chunk_size: int = 50_000,
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
        description=(
            "Backfill pending Postgres DB translations with English placeholders "
            "from Files/Mod/db1.txt. Status remains pending."
        ),
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL"),
        help="Postgres connection string. Defaults to DATABASE_URL, then .codex/config.toml.",
    )
    parser.add_argument(
        "--source",
        default="Files/Mod/db1.txt",
        help="English mod db1.txt path.",
    )
    parser.add_argument(
        "--converted-dir",
        default="Files/Converted",
        help="Directory containing converted dumpedPrefabText.txt and dynamicStrings.txt.",
    )
    parser.add_argument(
        "--skip-assets",
        action="store_true",
        help="Only backfill db1.txt fields; skip converted asset files.",
    )
    parser.add_argument(
        "--import-id",
        type=int,
        default=None,
        help="db_imports.id to match. Defaults to workflow_metadata.active_db_import.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write updates. Without this, only report what would change.",
    )
    parser.add_argument(
        "--sample-limit",
        type=int,
        default=20,
        help="Number of conflict/missing examples to print.",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=50_000,
        help="Rows per COPY chunk for parsed English fields.",
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


def normalize_line(raw_line: str) -> str:
    line = raw_line.rstrip("\n")
    return line.replace("\r", "")


def load_pending_positions(cursor, import_id: int):
    cursor.execute(
        """
        select
            tv.id as translation_value_id,
            tv.source_text as value_source_text,
            df.source_text as field_source_text,
            ds.name as section_name,
            dl.line_no,
            df.field_index
        from translation_values tv
        join translation_occurrences toc
          on toc.translation_value_id = tv.id
        join db_fields df
          on df.id = toc.db_field_id
        join db_lines dl
          on dl.id = df.line_id
        join db_sections ds
          on ds.id = dl.section_id
        where ds.import_id = %s
          and toc.kind = 'db_field'::occurrence_kind
          and tv.status = 'pending'::translation_status
        order by ds.section_order, dl.line_no, df.field_index
        """,
        (import_id,),
    )
    pending_by_key: dict[
        tuple[str, int, int],
        list[tuple[int, str, str]],
    ] = {}
    pending_values: dict[int, str] = {}
    pending_occurrences = 0
    for (
        translation_value_id,
        value_source_text,
        field_source_text,
        section_name,
        line_no,
        field_index,
    ) in cursor.fetchall():
        pending_occurrences += 1
        pending_values[translation_value_id] = value_source_text
        key = (section_name, line_no, field_index)
        pending_by_key.setdefault(key, []).append(
            (translation_value_id, value_source_text, field_source_text),
        )
    return pending_by_key, pending_values, pending_occurrences


def load_pending_asset_positions(cursor):
    cursor.execute(
        """
        select
            tv.id as translation_value_id,
            tv.source_text as value_source_text,
            ae.source_text as asset_source_text,
            ae.source_name,
            ae.entry_no
        from translation_values tv
        join translation_occurrences toc
          on toc.translation_value_id = tv.id
        join asset_entries ae
          on ae.id = toc.asset_entry_id
        where toc.kind in ('prefab_text'::occurrence_kind, 'dynamic_string'::occurrence_kind)
          and ae.source_name = any(%s)
          and tv.status = 'pending'::translation_status
        order by ae.source_name, ae.entry_no
        """,
        (list(ASSET_FILES),),
    )
    pending_by_key: dict[
        tuple[str, int],
        list[tuple[int, str, str]],
    ] = {}
    pending_values: dict[int, str] = {}
    pending_occurrences = 0
    for (
        translation_value_id,
        value_source_text,
        asset_source_text,
        source_name,
        entry_no,
    ) in cursor.fetchall():
        pending_occurrences += 1
        pending_values[translation_value_id] = value_source_text
        pending_by_key.setdefault((source_name, entry_no), []).append(
            (translation_value_id, value_source_text, asset_source_text),
        )
    return pending_by_key, pending_values, pending_occurrences


def load_mod_matches(
    path: Path,
    pending_by_key: dict[tuple[str, int, int], list[tuple[int, str, str]]],
    buffer: CopyBuffer,
) -> tuple[int, int, int, int]:
    sections = 0
    data_lines = 0
    fields = 0
    count_mismatches = 0
    current_section: str | None = None
    current_declared_count = 0
    current_line_no = 0

    def finish_section() -> None:
        nonlocal count_mismatches
        if current_section is not None and current_line_no != current_declared_count:
            count_mismatches += 1

    with path.open("r", encoding="utf-8-sig", newline="\n") as handle:
        for raw_line in handle:
            line = normalize_line(raw_line)
            if not line.strip():
                continue

            header = HEADER_RE.match(line)
            if header:
                finish_section()
                current_section = header.group(1)
                current_declared_count = int(header.group(2))
                current_line_no = 0
                sections += 1
                continue

            if current_section is None:
                raise ValueError(f"Data line before first section header: {line[:120]}")

            current_line_no += 1
            data_lines += 1
            parts = line.split("#")
            fields += len(parts)
            for field_index, text in enumerate(parts):
                pending_items = pending_by_key.get(
                    (current_section, current_line_no, field_index),
                )
                if not pending_items or text == "":
                    continue
                for translation_value_id, value_source_text, field_source_text in pending_items:
                    if text == field_source_text:
                        continue
                    buffer.append(
                        (
                            translation_value_id,
                            value_source_text,
                            text,
                            current_section,
                            current_line_no,
                            field_index,
                        )
                    )

    finish_section()
    buffer.flush()
    return sections, data_lines, fields, count_mismatches


def load_converted_asset_matches(
    converted_dir: Path,
    pending_by_key: dict[tuple[str, int], list[tuple[int, str, str]]],
    buffer: CopyBuffer,
) -> tuple[int, int, int, int]:
    files_seen = 0
    entries_seen = 0
    splits_seen = 0
    missing_files = 0

    for source_name in ASSET_FILES:
        path = converted_dir / source_name
        if not path.exists():
            missing_files += 1
            continue
        files_seen += 1
        with path.open("r", encoding="utf-8-sig") as handle:
            entries = yaml.safe_load(handle) or []

        for entry_no, entry in enumerate(entries, start=1):
            entries_seen += 1
            pending_items = pending_by_key.get((source_name, entry_no))
            if not pending_items:
                continue

            for split in entry.get("splits") or []:
                source_text = split.get("text")
                translated_text = split.get("translated")
                if source_text is None:
                    continue
                splits_seen += 1
                if translated_text is None or translated_text == "" or translated_text == source_text:
                    continue
                for (
                    translation_value_id,
                    value_source_text,
                    asset_source_text,
                ) in pending_items:
                    if source_text != asset_source_text:
                        continue
                    buffer.append(
                        (
                            translation_value_id,
                            value_source_text,
                            translated_text,
                            source_name,
                            entry_no,
                        )
                    )

    buffer.flush()
    return files_seen, entries_seen, splits_seen, missing_files


def print_samples(cursor, table_name: str, sample_limit: int, columns: Sequence[str]) -> None:
    if sample_limit <= 0:
        return
    cursor.execute(
        f"""
        select {", ".join(columns)}
        from {table_name}
        order by translation_value_id
        limit %s
        """,
        (sample_limit,),
    )
    rows = cursor.fetchall()
    if not rows:
        return
    print(f"{table_name}_samples:")
    print("\t".join(columns))
    for row in rows:
        print("\t".join("" if value is None else str(value) for value in row))


def main() -> int:
    args = parse_args()
    database_url = args.database_url or load_database_url_from_codex_config()
    if not database_url:
        print(
            "Missing database URL. Set DATABASE_URL or pass --database-url.",
            file=sys.stderr,
        )
        return 2

    source_path = Path(args.source)
    if not source_path.exists():
        print(f"Source db1.txt not found: {source_path}", file=sys.stderr)
        return 2

    with psycopg2.connect(database_url) as conn:
        with conn.cursor() as cursor:
            import_id = args.import_id or active_import_id(cursor)
            pending_by_key, pending_values, pending_occurrences = load_pending_positions(
                cursor,
                import_id,
            )

            cursor.execute(
                """
                create temp table pending_db_values_tmp (
                    translation_value_id bigint not null,
                    source_text text not null
                ) on commit preserve rows
                """
            )
            pending_buffer = CopyBuffer(
                cursor,
                "pending_db_values_tmp",
                ("translation_value_id", "source_text"),
                args.chunk_size,
            )
            for translation_value_id, source_text in pending_values.items():
                pending_buffer.append((translation_value_id, source_text))
            pending_buffer.flush()
            cursor.execute(
                "create index on pending_db_values_tmp(translation_value_id)"
            )

            cursor.execute(
                """
                create temp table mod_matches_tmp (
                    translation_value_id bigint not null,
                    source_text text not null,
                    translated_text text not null,
                    section_name text not null,
                    line_no integer not null,
                    field_index integer not null
                ) on commit preserve rows
                """
            )
            buffer = CopyBuffer(
                cursor,
                "mod_matches_tmp",
                (
                    "translation_value_id",
                    "source_text",
                    "translated_text",
                    "section_name",
                    "line_no",
                    "field_index",
                ),
                args.chunk_size,
            )
            sections, data_lines, fields, count_mismatches = load_mod_matches(
                source_path,
                pending_by_key,
                buffer,
            )
            cursor.execute("create index on mod_matches_tmp(translation_value_id)")

            cursor.execute(
                """
                create temp table mod_conflicts_tmp on commit preserve rows as
                select
                    translation_value_id,
                    min(source_text) as source_text,
                    count(distinct translated_text) as english_variants,
                    string_agg(
                        distinct translated_text,
                        ' || ' order by translated_text
                    ) as sample_translations
                from mod_matches_tmp
                group by translation_value_id
                having count(distinct translated_text) > 1
                """
            )
            cursor.execute(
                "create index on mod_conflicts_tmp(translation_value_id)"
            )

            cursor.execute(
                """
                create temp table mod_stable_updates_tmp on commit preserve rows as
                select
                    mm.translation_value_id,
                    min(mm.translated_text) as translated_text
                from mod_matches_tmp mm
                where not exists (
                    select 1
                    from mod_conflicts_tmp mc
                    where mc.translation_value_id = mm.translation_value_id
                )
                group by mm.translation_value_id
                """
            )
            cursor.execute(
                "create index on mod_stable_updates_tmp(translation_value_id)"
            )

            pending_asset_occurrences = 0
            converted_files_seen = 0
            converted_entries_seen = 0
            converted_splits_seen = 0
            converted_missing_files = 0
            if args.skip_assets:
                pending_asset_values = {}
                pending_asset_by_key = {}
            else:
                (
                    pending_asset_by_key,
                    pending_asset_values,
                    pending_asset_occurrences,
                ) = load_pending_asset_positions(cursor)

            cursor.execute(
                """
                create temp table pending_asset_values_tmp (
                    translation_value_id bigint not null,
                    source_text text not null
                ) on commit preserve rows
                """
            )
            pending_asset_buffer = CopyBuffer(
                cursor,
                "pending_asset_values_tmp",
                ("translation_value_id", "source_text"),
                args.chunk_size,
            )
            for translation_value_id, source_text in pending_asset_values.items():
                pending_asset_buffer.append((translation_value_id, source_text))
            pending_asset_buffer.flush()
            cursor.execute(
                "create index on pending_asset_values_tmp(translation_value_id)"
            )

            cursor.execute(
                """
                create temp table converted_asset_matches_tmp (
                    translation_value_id bigint not null,
                    source_text text not null,
                    translated_text text not null,
                    source_name text not null,
                    entry_no integer not null
                ) on commit preserve rows
                """
            )
            asset_buffer = CopyBuffer(
                cursor,
                "converted_asset_matches_tmp",
                (
                    "translation_value_id",
                    "source_text",
                    "translated_text",
                    "source_name",
                    "entry_no",
                ),
                args.chunk_size,
            )
            if not args.skip_assets:
                (
                    converted_files_seen,
                    converted_entries_seen,
                    converted_splits_seen,
                    converted_missing_files,
                ) = load_converted_asset_matches(
                    Path(args.converted_dir),
                    pending_asset_by_key,
                    asset_buffer,
                )
            else:
                asset_buffer.flush()
            cursor.execute(
                "create index on converted_asset_matches_tmp(translation_value_id)"
            )

            cursor.execute(
                """
                create temp table converted_asset_conflicts_tmp on commit preserve rows as
                select
                    translation_value_id,
                    min(source_text) as source_text,
                    count(distinct translated_text) as translated_variants,
                    string_agg(
                        distinct translated_text,
                        ' || ' order by translated_text
                    ) as sample_translations
                from converted_asset_matches_tmp
                group by translation_value_id
                having count(distinct translated_text) > 1
                """
            )
            cursor.execute(
                "create index on converted_asset_conflicts_tmp(translation_value_id)"
            )

            cursor.execute(
                """
                create temp table converted_asset_stable_updates_tmp on commit preserve rows as
                select
                    cam.translation_value_id,
                    min(cam.translated_text) as translated_text
                from converted_asset_matches_tmp cam
                where not exists (
                    select 1
                    from converted_asset_conflicts_tmp cac
                    where cac.translation_value_id = cam.translation_value_id
                )
                group by cam.translation_value_id
                """
            )
            cursor.execute(
                "create index on converted_asset_stable_updates_tmp(translation_value_id)"
            )

            cursor.execute(
                """
                create temp table mod_missing_tmp on commit preserve rows as
                select pdv.translation_value_id, pdv.source_text
                from pending_db_values_tmp pdv
                where not exists (
                    select 1
                    from mod_matches_tmp mm
                    where mm.translation_value_id = pdv.translation_value_id
                )
                """
            )

            cursor.execute(
                """
                create temp table converted_asset_missing_tmp on commit preserve rows as
                select pav.translation_value_id, pav.source_text
                from pending_asset_values_tmp pav
                where not exists (
                    select 1
                    from converted_asset_matches_tmp cam
                    where cam.translation_value_id = pav.translation_value_id
                )
                """
            )

            cursor.execute(
                """
                create temp table mod_source_fallback_tmp on commit preserve rows as
                select pdv.translation_value_id, pdv.source_text as translated_text
                from pending_db_values_tmp pdv
                where not exists (
                    select 1
                    from mod_stable_updates_tmp msu
                    where msu.translation_value_id = pdv.translation_value_id
                )
                and not exists (
                    select 1
                    from converted_asset_stable_updates_tmp casu
                    where casu.translation_value_id = pdv.translation_value_id
                )
                """
            )
            cursor.execute(
                "create index on mod_source_fallback_tmp(translation_value_id)"
            )

            cursor.execute(
                """
                create temp table converted_asset_source_fallback_tmp on commit preserve rows as
                select pav.translation_value_id, pav.source_text as translated_text
                from pending_asset_values_tmp pav
                where not exists (
                    select 1
                    from mod_stable_updates_tmp msu
                    where msu.translation_value_id = pav.translation_value_id
                )
                and not exists (
                    select 1
                    from converted_asset_stable_updates_tmp casu
                    where casu.translation_value_id = pav.translation_value_id
                )
                """
            )
            cursor.execute(
                "create index on converted_asset_source_fallback_tmp(translation_value_id)"
            )

            cursor.execute("select count(*) from pending_db_values_tmp")
            pending_db_values = cursor.fetchone()[0]
            cursor.execute("select count(*) from pending_asset_values_tmp")
            pending_asset_values_count = cursor.fetchone()[0]
            cursor.execute("select count(distinct translation_value_id) from mod_matches_tmp")
            matched_values = cursor.fetchone()[0]
            cursor.execute("select count(*) from mod_stable_updates_tmp")
            stable_values = cursor.fetchone()[0]
            cursor.execute("select count(*) from mod_conflicts_tmp")
            conflict_values = cursor.fetchone()[0]
            cursor.execute("select count(*) from mod_missing_tmp")
            missing_values = cursor.fetchone()[0]
            cursor.execute("select count(*) from mod_source_fallback_tmp")
            source_fallback_values = cursor.fetchone()[0]
            cursor.execute("select count(*) from mod_matches_tmp")
            matched_occurrences = cursor.fetchone()[0]
            cursor.execute("select count(*) from converted_asset_matches_tmp")
            converted_asset_matched_occurrences = cursor.fetchone()[0]
            cursor.execute(
                "select count(distinct translation_value_id) from converted_asset_matches_tmp"
            )
            converted_asset_matched_values = cursor.fetchone()[0]
            cursor.execute("select count(*) from converted_asset_stable_updates_tmp")
            converted_asset_stable_values = cursor.fetchone()[0]
            cursor.execute("select count(*) from converted_asset_conflicts_tmp")
            converted_asset_conflict_values = cursor.fetchone()[0]
            cursor.execute("select count(*) from converted_asset_missing_tmp")
            converted_asset_missing_values = cursor.fetchone()[0]
            cursor.execute("select count(*) from converted_asset_source_fallback_tmp")
            converted_asset_source_fallback_values = cursor.fetchone()[0]
            cursor.execute(
                """
                select count(distinct tv.id)
                from translation_values tv
                join translation_occurrences toc
                  on toc.translation_value_id = tv.id
                join asset_entries ae
                  on ae.id = toc.asset_entry_id
                where ae.source_name = 'dynamicStrings.txt'
                  and ae.raw_payload->>'type' = any(%s)
                  and tv.status = 'pending'::translation_status
                """,
                (list(UNSAFE_DYNAMIC_TYPES),),
            )
            unsafe_dynamic_values = cursor.fetchone()[0]

            updated_english_values = 0
            updated_converted_asset_values = 0
            updated_source_fallback_values = 0
            updated_asset_source_fallback_values = 0
            updated_global_source_fallback_values = 0
            ignored_unsafe_dynamic_values = 0
            if args.apply:
                cursor.execute(
                    """
                    update translation_values tv
                    set translated_text = msu.translated_text
                    from mod_stable_updates_tmp msu
                    where tv.id = msu.translation_value_id
                      and tv.status = 'pending'::translation_status
                      and tv.translated_text is distinct from msu.translated_text
                    """
                )
                updated_english_values = cursor.rowcount
                cursor.execute(
                    """
                    update translation_values tv
                    set translated_text = casu.translated_text
                    from converted_asset_stable_updates_tmp casu
                    where tv.id = casu.translation_value_id
                      and tv.status = 'pending'::translation_status
                      and not exists (
                          select 1
                          from mod_stable_updates_tmp msu
                          where msu.translation_value_id = tv.id
                      )
                      and tv.translated_text is distinct from casu.translated_text
                    """
                )
                updated_converted_asset_values = cursor.rowcount
                cursor.execute(
                    """
                    update translation_values tv
                    set translated_text = msf.translated_text
                    from mod_source_fallback_tmp msf
                    where tv.id = msf.translation_value_id
                      and tv.status = 'pending'::translation_status
                      and tv.translated_text is distinct from msf.translated_text
                    """
                )
                updated_source_fallback_values = cursor.rowcount
                cursor.execute(
                    """
                    update translation_values tv
                    set translated_text = casf.translated_text
                    from converted_asset_source_fallback_tmp casf
                    where tv.id = casf.translation_value_id
                      and tv.status = 'pending'::translation_status
                      and tv.translated_text is distinct from casf.translated_text
                    """
                )
                updated_asset_source_fallback_values = cursor.rowcount
                cursor.execute(
                    """
                    update translation_values
                    set translated_text = source_text
                    where status = 'pending'::translation_status
                      and (translated_text is null or translated_text = '')
                    """
                )
                updated_global_source_fallback_values = cursor.rowcount
                cursor.execute(
                    """
                    update translation_values tv
                    set status = 'ignored'::translation_status
                    from translation_occurrences toc
                    join asset_entries ae
                      on ae.id = toc.asset_entry_id
                    where toc.translation_value_id = tv.id
                      and ae.source_name = 'dynamicStrings.txt'
                      and ae.raw_payload->>'type' = any(%s)
                      and tv.status = 'pending'::translation_status
                    """,
                    (list(UNSAFE_DYNAMIC_TYPES),),
                )
                ignored_unsafe_dynamic_values = cursor.rowcount
                conn.commit()
            else:
                conn.rollback()

            print(f"source: {source_path}")
            print(f"mode: {'apply' if args.apply else 'dry-run'}")
            print(f"import_id: {import_id}")
            print(f"mod_sections: {sections}")
            print(f"mod_data_lines: {data_lines}")
            print(f"mod_fields: {fields}")
            print(f"mod_section_count_mismatches: {count_mismatches}")
            print(f"pending_db_values: {pending_db_values}")
            print(f"pending_db_occurrences: {pending_occurrences}")
            print(f"matched_occurrences: {matched_occurrences}")
            print(f"matched_values: {matched_values}")
            print(f"stable_update_values: {stable_values}")
            print(f"conflict_values_skipped: {conflict_values}")
            print(f"missing_or_untranslated_values_skipped: {missing_values}")
            print(f"source_fallback_values: {source_fallback_values}")
            print(f"converted_dir: {args.converted_dir}")
            print(f"converted_asset_files_seen: {converted_files_seen}")
            print(f"converted_asset_missing_files: {converted_missing_files}")
            print(f"converted_asset_entries: {converted_entries_seen}")
            print(f"converted_asset_splits: {converted_splits_seen}")
            print(f"pending_asset_values: {pending_asset_values_count}")
            print(f"pending_asset_occurrences: {pending_asset_occurrences}")
            print(
                "converted_asset_matched_occurrences: "
                f"{converted_asset_matched_occurrences}"
            )
            print(f"converted_asset_matched_values: {converted_asset_matched_values}")
            print(f"converted_asset_stable_values: {converted_asset_stable_values}")
            print(f"converted_asset_conflict_values: {converted_asset_conflict_values}")
            print(f"converted_asset_missing_values: {converted_asset_missing_values}")
            print(
                "converted_asset_source_fallback_values: "
                f"{converted_asset_source_fallback_values}"
            )
            print(f"unsafe_dynamic_values: {unsafe_dynamic_values}")
            print(f"updated_english_values: {updated_english_values}")
            print(f"updated_converted_asset_values: {updated_converted_asset_values}")
            print(f"updated_source_fallback_values: {updated_source_fallback_values}")
            print(
                "updated_asset_source_fallback_values: "
                f"{updated_asset_source_fallback_values}"
            )
            print(
                "updated_global_source_fallback_values: "
                f"{updated_global_source_fallback_values}"
            )
            print(f"ignored_unsafe_dynamic_values: {ignored_unsafe_dynamic_values}")
            print(
                "updated_values: "
                f"{updated_english_values + updated_converted_asset_values + updated_source_fallback_values + updated_asset_source_fallback_values + updated_global_source_fallback_values}"
            )
            print("status_changed: 0")

            if args.apply:
                print_samples(
                    cursor,
                    "mod_conflicts_tmp",
                    args.sample_limit,
                    (
                        "translation_value_id",
                        "source_text",
                        "english_variants",
                        "sample_translations",
                    ),
                )
                print_samples(
                    cursor,
                    "mod_missing_tmp",
                    args.sample_limit,
                    ("translation_value_id", "source_text"),
                )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
