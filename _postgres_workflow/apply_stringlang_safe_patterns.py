#!/usr/bin/env python3
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
from psycopg2.extras import execute_values

from apply_hanviet_name_batch import HAN_RE, database_url, hanviet, load_char_map


COLOR_RE = re.compile(r"^在(<color=&&00ff00ff>)(.*?)(</color>)(处)?有概率获得$")
EXPLORE_RE = re.compile(r"^在(<color=&&00ff00ff>)(.*?)(</color>)探索有概率获得$")
MERIDIAN_RE = re.compile(r"^此为江湖中流传的经脉图谱，在打通(.+?)经脉时参悟，可以带来额外效果。$")
BOOK_ATTR_RE = re.compile(
    r"^此书乃先代某无门派高人所著，通过独特的经脉图，讲解气在经脉中的运行方式，与正确的经脉知识搭配使用，能附加固定的(.+?)属性。$"
)


ATTR_MAP = {
    "命中": "Mệnh Trung",
    "·刚": "Cương",
    "要害": "Yếu Hại",
    "专注": "Chuyên Chú",
    "体魄": "Thể Phách",
    "内攻": "Nội Công",
    "诛心": "Tru Tâm",
    "·柔": "Nhu",
    "外攻": "Ngoại Công",
    "·毒": "Độc",
    "力道": "Lực Đạo",
    "闪避": "Né tránh",
    "·阳": "Dương",
    "内力": "Nội lực",
    "御心": "Ngự Tâm",
    "气血": "Khí huyết",
    "化解": "Hóa giải",
}


FIXED = {
    "一本传承数代的古书，书页已泛黄陈旧。但若能通读一遍，可以提升些许悟性。": "Một quyển cổ thư truyền qua nhiều đời, trang sách đã ố vàng cũ kỹ. Nếu có thể đọc thông một lượt, có thể tăng chút ngộ tính.",
    "凝结于寒潭之中的丹药，于境界提升有所裨益。": "Đan dược kết tụ trong hàn đàm, có ích cho việc tăng cảnh giới.",
    "龙涎丹是江湖中的珍贵丹药，此丹香气芬芳，内力波动惊人，可宁神静气，化解内力阻滞，使习武者能更加顺利的修炼武学，乃江湖中人竞相争夺之物。": "Long Tiên Đan là đan dược trân quý trong giang hồ. Đan này hương thơm thanh nhã, nội lực dao động kinh người, có thể an thần tĩnh khí, hóa giải nội lực đình trệ, giúp người luyện võ tu luyện võ học thuận lợi hơn, là vật người trong giang hồ tranh nhau đoạt lấy.",
    "在游戏江湖的过程中，博览武学，有概率参悟获得": "Trong quá trình du ngoạn giang hồ, khi đọc rộng võ học, có xác suất tham ngộ nhận được.",
}


def translate(source: str, char_map: dict[str, str]) -> str | None:
    if source in FIXED:
        return FIXED[source]

    match = MERIDIAN_RE.match(source)
    if match:
        name = hanviet(match.group(1), char_map)
        return f"Đây là kinh mạch đồ phổ lưu truyền trong giang hồ. Khi khai thông kinh mạch {name}, tham ngộ có thể đem lại hiệu quả bổ sung."

    match = BOOK_ATTR_RE.match(source)
    if match:
        attr = ATTR_MAP.get(match.group(1), hanviet(match.group(1), char_map))
        return f"Sách này do một cao nhân vô môn phái đời trước viết, dùng kinh mạch đồ độc đáo để giảng giải cách khí vận hành trong kinh mạch. Khi dùng cùng tri thức kinh mạch chính xác, có thể phụ thêm thuộc tính {attr} cố định."

    match = COLOR_RE.match(source)
    if match:
        open_tag, name, close_tag, _ = match.groups()
        translated_name = hanviet(name, char_map) if name else ""
        return f"Có xác suất nhận được tại {open_tag}{translated_name}{close_tag}."

    match = EXPLORE_RE.match(source)
    if match:
        open_tag, name, close_tag = match.groups()
        translated_name = hanviet(name, char_map) if name else ""
        return f"Có xác suất nhận được khi thăm dò {open_tag}{translated_name}{close_tag}."

    return None


def main() -> int:
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
              and (
                tv.source_text ~ '^此为江湖中流传的经脉图谱，在打通.+经脉时参悟，可以带来额外效果。$'
                or tv.source_text ~ '^此书乃先代某无门派高人所著，通过独特的经脉图，讲解气在经脉中的运行方式，与正确的经脉知识搭配使用，能附加固定的.+属性。$'
                or tv.source_text ~ '^在<color=&&00ff00ff>.*</color>(处)?有概率获得$'
                or tv.source_text ~ '^在<color=&&00ff00ff>.*</color>探索有概率获得$'
                or tv.source_text = any(%s)
              )
            order by tv.id
            """,
            (list(FIXED.keys()),),
        )
        rows = cur.fetchall()
        updates = [(row_id, translate(source, char_map)) for row_id, source in rows]
        updates = [(row_id, translated) for row_id, translated in updates if translated]
        bad = [
            (row_id, translated)
            for row_id, translated in updates
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
    with Path("_working/translation_batches.md").open("a", encoding="utf-8") as handle:
        handle.write(f"\n## {timestamp}\n")
        handle.write("- Mode: apply/stringlang-safe-patterns\n")
        handle.write(f"- Attempted: {attempted}\n")
        handle.write(f"- Updated: {attempted}\n")
        handle.write(f"- Candidate values: {attempted}\n")
        handle.write("- Conflicts skipped: 0\n")
        handle.write("- Reviewed leftover Han: 0\n")
        handle.write(f"- Pending remaining DB non-dialogue: {pending_non_dialogue}\n")
        handle.write(f"- Sections touched: stringlang:{attempted}\n")
        handle.write("- Notes: meridian manuals, fixed item descriptions, and acquisition-location strings; dialogue-like strings skipped.\n")
    print(f"attempted {attempted}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
