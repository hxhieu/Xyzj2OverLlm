#!/usr/bin/env python3
"""Import canonical db1.txt shape into the Postgres translation schema."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

import psycopg2


HEADER_RE = re.compile(r"^([^#|]+)\|([0-9]+)$")
HAN_RE = re.compile(r"[\u4e00-\u9fff]")
INT_RE = re.compile(r"^-?[0-9]+$")


@dataclass
class ImportStats:
    file_size: int = 0
    sha256: str = ""
    total_lines: int = 0
    sections: int = 0
    data_lines: int = 0
    fields: int = 0
    stored_fields: int = 0
    han_fields: int = 0
    translatable_fields: int = 0
    count_mismatches: int = 0


@dataclass
class CurrentSection:
    order: int
    name: str
    declared_count: int
    actual_count: int = 0


class CopyBuffer:
    def __init__(
        self,
        cursor,
        table_name: str,
        columns: Sequence[str],
        chunk_size: int,
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


class CommittingCopyBuffer(CopyBuffer):
    def __init__(
        self,
        cursor,
        table_name: str,
        columns: Sequence[str],
        chunk_size: int,
        commit_every_rows: int,
        before_flush: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(cursor, table_name, columns, chunk_size)
        self.commit_every_rows = commit_every_rows
        self._rows_since_commit = 0
        self.before_flush = before_flush

    def flush(self) -> None:
        pending = len(self.rows)
        if not pending:
            return
        if self.before_flush is not None:
            self.before_flush()
        super().flush()
        self._rows_since_commit += pending
        if self._rows_since_commit >= self.commit_every_rows:
            self.cursor.connection.commit()
            self._rows_since_commit = 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import canonical db1.txt section/line/field shape into Postgres.",
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL"),
        help="Postgres connection string. Defaults to DATABASE_URL.",
    )
    parser.add_argument(
        "--source",
        default="Files/Raw/DB/db1.txt",
        help="Path to canonical source db1.txt.",
    )
    parser.add_argument(
        "--all-fields",
        action="store_true",
        help="Store every field in db_fields. Default stores only translatable fields.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write to Postgres. Without this, only parses and reports stats.",
    )
    parser.add_argument(
        "--replace-existing",
        action="store_true",
        help="Delete existing import with the same sha256 before inserting.",
    )
    parser.add_argument(
        "--allow-count-mismatch",
        action="store_true",
        help="Allow section declared line counts to differ from parsed counts.",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=50_000,
        help="Rows per COPY chunk.",
    )
    parser.add_argument(
        "--field-commit-every",
        type=int,
        default=1_000_000,
        help="Commit field COPY progress after this many rows.",
    )
    parser.add_argument(
        "--rebuild-indexes",
        action="store_true",
        help="Drop secondary indexes before import and recreate them after import.",
    )
    parser.add_argument(
        "--note",
        default="canonical raw db1 import",
        help="Note stored in db_imports.",
    )
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_line(raw_line: str) -> str:
    line = raw_line.rstrip("\n")
    return line.replace("\r", "")


def source_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def is_translatable(text: str) -> bool:
    return bool(text.strip()) and bool(HAN_RE.search(text))


def game_id_from_fields(fields: Sequence[str]) -> str | None:
    if not fields:
        return None
    first = fields[0].strip()
    if INT_RE.match(first):
        return first
    return None


def parse_db1(
    path: Path,
    *,
    store_all_fields: bool = False,
    section_buffer: CopyBuffer | None = None,
    line_buffer: CopyBuffer | None = None,
    field_buffer: CopyBuffer | None = None,
) -> ImportStats:
    stats = ImportStats(file_size=path.stat().st_size, sha256=sha256_file(path))
    current: CurrentSection | None = None
    section_order = 0
    line_no = 0

    def finish_section() -> None:
        nonlocal current
        if current is None:
            return
        if current.actual_count != current.declared_count:
            stats.count_mismatches += 1
        if section_buffer is not None:
            section_buffer.append(
                (
                    current.order,
                    current.name,
                    current.declared_count,
                    current.actual_count,
                )
            )

    # The original C# splitter uses Split('\n') and then removes stray '\r'
    # characters inside local_text_string-like rows. Do the same here; Python's
    # universal newline mode would incorrectly split those embedded '\r' bytes
    # into extra records.
    with path.open("r", encoding="utf-8-sig", newline="\n") as handle:
        for raw_line in handle:
            line = normalize_line(raw_line)
            if not line.strip():
                continue

            stats.total_lines += 1
            header = HEADER_RE.match(line)
            if header:
                finish_section()
                section_order += 1
                line_no = 0
                current = CurrentSection(
                    order=section_order,
                    name=header.group(1),
                    declared_count=int(header.group(2)),
                )
                stats.sections += 1
                continue

            if current is None:
                raise ValueError(f"Data line before first section header: {line[:120]}")

            line_no += 1
            current.actual_count += 1
            stats.data_lines += 1

            fields = line.split("#")
            stats.fields += len(fields)

            if line_buffer is not None:
                line_buffer.append(
                    (
                        current.order,
                        line_no,
                        line,
                        len(fields),
                        game_id_from_fields(fields),
                    )
                )

            for field_index, text in enumerate(fields):
                has_han = bool(HAN_RE.search(text))
                translatable = is_translatable(text)
                if has_han:
                    stats.han_fields += 1
                if translatable:
                    stats.translatable_fields += 1
                if store_all_fields or translatable:
                    stats.stored_fields += 1
                if field_buffer is not None and (store_all_fields or translatable):
                    field_buffer.append(
                        (
                            current.order,
                            line_no,
                            field_index,
                            text,
                            source_hash(text),
                            has_han,
                            translatable,
                        )
                    )

    finish_section()
    return stats


def print_stats(stats: ImportStats) -> None:
    print(f"sha256: {stats.sha256}")
    print(f"size_bytes: {stats.file_size}")
    print(f"total_nonblank_lines: {stats.total_lines}")
    print(f"sections: {stats.sections}")
    print(f"data_lines: {stats.data_lines}")
    print(f"fields: {stats.fields}")
    print(f"stored_fields: {stats.stored_fields}")
    print(f"han_fields: {stats.han_fields}")
    print(f"translatable_fields: {stats.translatable_fields}")
    print(f"section_count_mismatches: {stats.count_mismatches}")


def reserve_ids(cursor, sequence_name: str, count: int) -> int:
    if count <= 0:
        raise ValueError("Cannot reserve an empty id range")
    cursor.execute("select nextval(%s)", (sequence_name,))
    start_id = cursor.fetchone()[0]
    end_id = start_id + count - 1
    cursor.execute("select setval(%s, %s, true)", (sequence_name, end_id))
    return start_id


def stream_direct_import(
    path: Path,
    *,
    section_buffer: CopyBuffer,
    line_buffer: CopyBuffer,
    field_buffer: CopyBuffer,
    import_id: int,
    section_id_base: int,
    line_id_base: int,
    field_id_base: int,
    store_all_fields: bool,
) -> ImportStats:
    stats = ImportStats(file_size=path.stat().st_size, sha256=sha256_file(path))
    current: CurrentSection | None = None
    section_order = 0
    line_no = 0
    absolute_line_index = 0
    absolute_field_index = 0

    def section_id(order: int) -> int:
        return section_id_base + order - 1

    def line_id(index: int) -> int:
        return line_id_base + index - 1

    def field_id(index: int) -> int:
        return field_id_base + index - 1

    def finish_section() -> None:
        nonlocal current
        if current is None:
            return
        if current.actual_count != current.declared_count:
            stats.count_mismatches += 1

    def start_section(name: str, declared_count: int) -> None:
        nonlocal current, section_order, line_no
        section_order += 1
        line_no = 0
        current = CurrentSection(
            order=section_order,
            name=name,
            declared_count=declared_count,
        )
        stats.sections += 1
        section_buffer.append(
            (
                section_id(current.order),
                import_id,
                current.order,
                current.name,
                current.declared_count,
                current.declared_count,
            )
        )
        # db_lines has a FK to db_sections, so the section row must be visible
        # before the first line for that section is copied.
        section_buffer.flush()

    with path.open("r", encoding="utf-8-sig", newline="\n") as handle:
        for raw_line in handle:
            line = normalize_line(raw_line)
            if not line.strip():
                continue

            stats.total_lines += 1
            header = HEADER_RE.match(line)
            if header:
                finish_section()
                start_section(header.group(1), int(header.group(2)))
                continue

            if current is None:
                raise ValueError(f"Data line before first section header: {line[:120]}")

            line_no += 1
            absolute_line_index += 1
            current.actual_count += 1
            stats.data_lines += 1

            fields = line.split("#")
            stats.fields += len(fields)
            current_line_id = line_id(absolute_line_index)

            line_buffer.append(
                (
                    current_line_id,
                    section_id(current.order),
                    line_no,
                    line,
                    len(fields),
                    game_id_from_fields(fields),
                )
            )

            for field_index, text in enumerate(fields):
                absolute_field_index += 1
                has_han = bool(HAN_RE.search(text))
                translatable = is_translatable(text)
                if has_han:
                    stats.han_fields += 1
                if translatable:
                    stats.translatable_fields += 1
                if store_all_fields or translatable:
                    stats.stored_fields += 1
                    field_buffer.append(
                        (
                            field_id(stats.stored_fields),
                            current_line_id,
                            field_index,
                            text,
                            source_hash(text),
                            has_han,
                            translatable,
                        )
                    )

    finish_section()
    return stats


def create_temp_tables(cursor) -> None:
    cursor.execute(
        """
        create temp table import_sections_tmp (
            section_order integer not null,
            name text not null,
            declared_line_count integer not null,
            actual_line_count integer not null
        ) on commit drop;

        create temp table import_lines_tmp (
            section_order integer not null,
            line_no integer not null,
            raw_text text not null,
            field_count integer not null,
            game_id text
        ) on commit drop;

        create temp table import_fields_tmp (
            section_order integer not null,
            line_no integer not null,
            field_index integer not null,
            source_text text not null,
            source_sha256 text not null,
            has_han boolean not null,
            is_probably_translatable boolean not null
        ) on commit drop;
        """
    )


def create_temp_indexes(cursor) -> None:
    cursor.execute(
        """
        create index on import_sections_tmp(section_order);
        create index on import_lines_tmp(section_order, line_no);
        create index on import_fields_tmp(section_order, line_no);
        """
    )


SECONDARY_INDEX_DDL = {
    "idx_db_sections_import_order": (
        "create index idx_db_sections_import_order "
        "on db_sections(import_id, section_order)"
    ),
    "idx_db_lines_section_line": (
        "create index idx_db_lines_section_line "
        "on db_lines(section_id, line_no)"
    ),
    "idx_db_lines_game_id": (
        "create index idx_db_lines_game_id "
        "on db_lines(game_id) where game_id is not null"
    ),
    "idx_db_fields_line_field": (
        "create index idx_db_fields_line_field "
        "on db_fields(line_id, field_index)"
    ),
    "idx_db_fields_source_sha256": (
        "create index idx_db_fields_source_sha256 "
        "on db_fields(source_sha256)"
    ),
    "idx_db_fields_has_han": (
        "create index idx_db_fields_has_han "
        "on db_fields(has_han) where has_han"
    ),
    "idx_asset_entries_source_sha256": (
        "create index idx_asset_entries_source_sha256 "
        "on asset_entries(source_sha256)"
    ),
    "idx_translation_values_status": (
        "create index idx_translation_values_status "
        "on translation_values(status)"
    ),
    "idx_translation_values_source_sha_context": (
        "create index idx_translation_values_source_sha_context "
        "on translation_values(source_sha256, context_key)"
    ),
    "idx_translation_occurrences_status": (
        "create index idx_translation_occurrences_status "
        "on translation_occurrences(status)"
    ),
    "idx_translation_occurrences_value": (
        "create index idx_translation_occurrences_value "
        "on translation_occurrences(translation_value_id)"
    ),
}


def drop_secondary_indexes(cursor) -> None:
    for index_name in SECONDARY_INDEX_DDL:
        cursor.execute(f"drop index if exists {index_name}")


def recreate_secondary_indexes(cursor) -> None:
    for ddl in SECONDARY_INDEX_DDL.values():
        cursor.execute(ddl)


def recreate_secondary_indexes_with_new_connection(database_url: str) -> None:
    with psycopg2.connect(database_url) as conn:
        with conn.cursor() as cursor:
            recreate_secondary_indexes(cursor)


def apply_import(args: argparse.Namespace, path: Path, dry_stats: ImportStats) -> None:
    if not args.database_url:
        raise ValueError("--database-url or DATABASE_URL is required with --apply")

    indexes_dropped = False
    try:
        with psycopg2.connect(args.database_url) as conn:
            with conn.cursor() as cursor:
                cursor.execute("set synchronous_commit to off")
                if args.rebuild_indexes:
                    drop_secondary_indexes(cursor)
                    conn.commit()
                    indexes_dropped = True

                cursor.execute(
                    "select id from db_imports where source_sha256 = %s",
                    (dry_stats.sha256,),
                )
                existing = cursor.fetchone()
                if existing and not args.replace_existing:
                    raise ValueError(
                        "This source sha256 is already imported as db_imports.id="
                        f"{existing[0]}. Use --replace-existing to replace it."
                    )
                if existing and args.replace_existing:
                    cursor.execute("delete from db_imports where id = %s", (existing[0],))

                cursor.execute(
                    """
                    insert into db_imports (
                        source_path,
                        source_sha256,
                        source_size_bytes,
                        note
                    )
                    values (%s, %s, %s, %s)
                    returning id
                    """,
                    (str(path), dry_stats.sha256, dry_stats.file_size, args.note),
                )
                import_id = cursor.fetchone()[0]
                section_id_base = reserve_ids(cursor, "db_sections_id_seq", dry_stats.sections)
                line_id_base = reserve_ids(cursor, "db_lines_id_seq", dry_stats.data_lines)
                fields_to_store = (
                    dry_stats.fields if args.all_fields else dry_stats.translatable_fields
                )
                field_id_base = reserve_ids(cursor, "db_fields_id_seq", fields_to_store)

                section_buffer = CopyBuffer(
                    cursor,
                    "db_sections",
                    (
                        "id",
                        "import_id",
                        "section_order",
                        "name",
                        "declared_line_count",
                        "actual_line_count",
                    ),
                    args.chunk_size,
                )
                line_buffer = CopyBuffer(
                    cursor,
                    "db_lines",
                    ("id", "section_id", "line_no", "raw_text", "field_count", "game_id"),
                    args.chunk_size,
                )
                field_buffer = CommittingCopyBuffer(
                    cursor,
                    "db_fields",
                    (
                        "id",
                        "line_id",
                        "field_index",
                        "source_text",
                        "source_sha256",
                        "has_han",
                        "is_probably_translatable",
                    ),
                    args.chunk_size,
                    args.field_commit_every,
                    before_flush=line_buffer.flush,
                )

                write_stats = stream_direct_import(
                    path,
                    import_id=import_id,
                    section_id_base=section_id_base,
                    line_id_base=line_id_base,
                    field_id_base=field_id_base,
                    store_all_fields=args.all_fields,
                    section_buffer=section_buffer,
                    line_buffer=line_buffer,
                    field_buffer=field_buffer,
                )
                section_buffer.flush()
                line_buffer.flush()
                field_buffer.flush()

                if write_stats.count_mismatches and not args.allow_count_mismatch:
                    raise ValueError(
                        "Section declared/actual line count mismatch. "
                        "Use --allow-count-mismatch only after inspection."
                    )
                conn.commit()

                cursor.execute(
                    """
                    insert into workflow_metadata(key, value)
                    values (
                        'active_db_import',
                        jsonb_build_object(
                            'import_id', %s,
                            'source_path', %s,
                            'source_sha256', %s
                        )
                    )
                    on conflict (key) do update
                    set value = excluded.value
                    """,
                    (import_id, str(path), dry_stats.sha256),
                )

                cursor.execute(
                    """
                    select
                        (select count(*) from db_sections where import_id = %s) as sections,
                        (
                            select count(*)
                            from db_lines dl
                            join db_sections ds on ds.id = dl.section_id
                            where ds.import_id = %s
                        ) as lines,
                        (
                            select count(*)
                            from db_fields df
                            join db_lines dl on dl.id = df.line_id
                            join db_sections ds on ds.id = dl.section_id
                            where ds.import_id = %s
                        ) as fields
                    """,
                    (import_id, import_id, import_id),
                )
                counts = cursor.fetchone()
    except Exception:
        if indexes_dropped:
            recreate_secondary_indexes_with_new_connection(args.database_url)
        raise

    print(f"import_id: {import_id}")
    print(f"inserted_sections: {counts[0]}")
    print(f"inserted_lines: {counts[1]}")
    print(f"inserted_fields: {counts[2]}")

    if args.rebuild_indexes:
        print("rebuilding_secondary_indexes: true")
        recreate_secondary_indexes_with_new_connection(args.database_url)


def main() -> int:
    args = parse_args()
    path = Path(args.source).resolve()
    if not path.exists():
        print(f"Source file not found: {path}", file=sys.stderr)
        return 2

    stats = parse_db1(path, store_all_fields=args.all_fields)
    print_stats(stats)

    if stats.count_mismatches and not args.allow_count_mismatch:
        print(
            "Refusing to apply because at least one section count mismatches.",
            file=sys.stderr,
        )
        return 1

    if not args.apply:
        print("dry_run: true")
        return 0

    apply_import(args, path, stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
