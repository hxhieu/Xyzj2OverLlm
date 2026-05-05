#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
from psycopg2.extras import execute_values

from apply_hanviet_name_batch import HAN_RE, database_url, hanviet, load_char_map


EQUIPMENT_RE = re.compile(r"^(.+),([0-9]+)级装备$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--report", default="_working/translation_batches.md")
    return parser.parse_args()


def translate(source: str, char_map: dict[str, str]) -> str:
    match = EQUIPMENT_RE.match(source)
    if not match:
        raise ValueError(source)
    name, level = match.groups()
    return f"{hanviet(name, char_map)}, trang bị cấp {level}"


def main() -> int:
    args = parse_args()
    char_map = load_char_map()
    db_url = database_url()
    with psycopg2.connect(db_url) as conn, conn.cursor() as cur:
        cur.execute(
            """
            select distinct on (tv.id) tv.id, tv.source_text
            from translation_values tv
            join translation_occurrences toc on toc.translation_value_id = tv.id
            join db_fields df on df.id = toc.db_field_id
            join db_lines dl on dl.id = df.line_id
            join db_sections ds on ds.id = dl.section_id
            where tv.status = 'pending'
              and ds.name = 'stringlang'
              and tv.source_text ~ '^[^，。！？；：、]+,[0-9]+级装备$'
            order by tv.id
            limit %s
            """,
            (args.limit,),
        )
        rows = cur.fetchall()
        updates = [(row_id, translate(source, char_map)) for row_id, source in rows]
        bad = [
            (row_id, source, translated)
            for (row_id, source), (_, translated) in zip(rows, updates)
            if HAN_RE.search(translated)
        ]
        if bad:
            print(f"unconverted {len(bad)}")
            for item in bad[:50]:
                print("\t".join(str(part) for part in item))
            return 1
        execute_values(
            cur,
            """
            update translation_values tv
            set translated_text = data.translated_text,
                status = 'reviewed',
                updated_at = now()
            from (values %s) as data(id, translated_text)
            where tv.id = data.id and tv.status = 'pending'
            """,
            updates,
        )
        attempted = len(updates)
    with psycopg2.connect(db_url) as conn, conn.cursor() as cur:
        cur.execute(
            """
            select
                count(distinct tv.id) filter (where tv.status = 'pending') as pending_equipment,
                count(distinct tv.id) filter (
                    where tv.status = 'reviewed' and tv.translated_text ~ '[一-鿿]'
                ) as reviewed_han
            from translation_values tv
            join translation_occurrences toc on toc.translation_value_id = tv.id
            join db_fields df on df.id = toc.db_field_id
            join db_lines dl on dl.id = df.line_id
            join db_sections ds on ds.id = dl.section_id
            where ds.name = 'stringlang'
              and tv.source_text ~ '^[^，。！？；：、]+,[0-9]+级装备$'
            """
        )
        pending_equipment, reviewed_han = cur.fetchone()
        cur.execute(
            """
            with dialogue_sections(name) as (
                values ('ai_dialog'), ('npc_interact'), ('npc_interact_dangmojianghu')
            )
            select count(distinct tv.id)
            from translation_values tv
            join translation_occurrences toc on toc.translation_value_id = tv.id
            join db_fields df on df.id = toc.db_field_id
            join db_lines dl on dl.id = df.line_id
            join db_sections ds on ds.id = dl.section_id
            left join dialogue_sections x on x.name = ds.name
            where tv.status = 'pending' and x.name is null
            """
        )
        pending_non_dialogue = cur.fetchone()[0]

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    with Path(args.report).open("a", encoding="utf-8") as handle:
        handle.write(f"\n## {timestamp}\n")
        handle.write("- Mode: apply/stringlang-equipment\n")
        handle.write(f"- Attempted: {attempted}\n")
        handle.write(f"- Updated: {attempted}\n")
        handle.write(f"- Candidate values: {attempted}\n")
        handle.write("- Conflicts skipped: 0\n")
        handle.write(f"- Reviewed leftover Han: {reviewed_han}\n")
        handle.write(f"- Pending remaining DB non-dialogue: {pending_non_dialogue}\n")
        handle.write(f"- Sections touched: stringlang:{attempted}\n")
        handle.write(
            "- Notes: stringlang equipment pattern `Tên,N级装备`; "
            f"{pending_equipment} equipment-pattern pending remain; "
            "dialogue_sections_excluded=['ai_dialog', 'npc_interact', 'npc_interact_dangmojianghu']\n"
        )
    print(f"attempted {attempted}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
