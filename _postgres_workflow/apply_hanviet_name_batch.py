#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
import unicodedata
from pathlib import Path

import psycopg2
from psycopg2.extras import execute_values


HAN_RE = re.compile(r"[一-鿿]")


EXTRA_CHAR_MAP = {
    "阎": "diêm",
    "闫": "diêm",
    "峰": "phong",
    "坨": "đà",
    "沉": "trầm",
    "荵": "nhẫn",
    "镇": "trấn",
    "珈": "gia",
    "捕": "bộ",
    "锆": "cáo",
    "冼": "tiển",
    "皂": "tạo",
    "枦": "lô",
    "巅": "điên",
    "启": "khải",
    "坞": "ổ",
    "伢": "nha",
    "步": "bộ",
    "尚": "thượng",
    "煮": "chử",
    "挡": "đáng",
    "瓮": "úng",
    "弯": "loan",
    "铧": "hoa",
    "煸": "biên",
    "氖": "nãi",
    "败": "bại",
    "击": "kích",
    "剑": "kiếm",
    "补": "bổ",
    "轻": "khinh",
    "关": "quan",
    "难": "nan",
    "体": "thể",
    "宝": "bảo",
    "兴": "hưng",
    "龙": "long",
    "门": "môn",
    "风": "phong",
    "华": "hoa",
    "叁": "tam",
    "卟": "bốc",
    "饮": "ẩm",
    "馗": "quỳ",
    "锗": "giả",
    "瓶": "bình",
    "赏": "thưởng",
    "研": "nghiên",
    "增": "tăng",
    "囊": "nang",
    "储": "trữ",
    "擐": "hoạn",
    "妹": "muội",
    "慎": "thận",
    "谣": "dao",
    "钰": "ngọc",
    "杰": "kiệt",
    "骁": "kiêu",
    "峯": "phong",
    "栖": "tê",
    "啸": "khiếu",
    "勋": "huân",
    "镖": "tiêu",
    "厨": "trù",
    "馆": "quán",
    "铺": "phô",
    "帮": "bang",
    "驿": "dịch",
    "坛": "đàn",
    "庙": "miếu",
    "阁": "các",
    "庄": "trang",
    "观": "quán",
    "斋": "trai",
    "营": "doanh",
    "寨": "trại",
    "渔": "ngư",
    "妇": "phụ",
    "贩": "phiến",
    "贼": "tặc",
    "匪": "phỉ",
    "僧": "tăng",
    "医": "y",
    "药": "dược",
    "矿": "quáng",
    "银": "ngân",
    "铜": "đồng",
    "铁": "thiết",
    "锡": "tích",
    "婧": "tịnh",
    "璟": "cảnh",
    "啰": "la",
    "圈": "quyển",
    "锐": "duệ",
    "崂": "lao",
    "洺": "minh",
    "梯": "thê",
    "颐": "di",
    "熙": "hi",
    "告": "cáo",
    "郎": "lang",
    "吆": "yêu",
    "澹": "đạm",
    "棕": "tông",
    "伪": "ngụy",
    "媞": "đề",
    "犸": "mã",
    "亓": "kì",
    "沣": "phong",
    "砖": "chuyên",
    "嫣": "yên",
    "昀": "vân",
    "佬": "lão",
    "弼": "bật",
    "污": "ô",
    "赑": "bí",
    "屃": "hí",
    "娴": "nhàn",
    "荔": "lệ",
    "珺": "quận",
    "猹": "tra",
    "玥": "nguyệt",
    "孬": "nao",
    "胼": "biền",
    "嵇": "kê",
    "惮": "đạn",
    "昝": "tản",
    "侁": "sân",
    "夯": "bổn",
    "燚": "dịch",
    "搞": "cảo",
    "躲": "đóa",
    "她": "tha",
    "部": "bộ",
    "辣": "lạt",
    "讧": "hống",
    "坑": "khanh",
    "莼": "thuần",
    "值": "trực",
    "捡": "kiểm",
    "溜": "lưu",
    "么": "ma",
    "甩": "suất",
}


PHRASE_MAP = {
    "光圈": "Quang Quyển",
    "告别": "Cáo Biệt",
    "工具人": "Công Cụ Nhân",
    "十二连环坞": "Thập Nhị Liên Hoàn Ổ",
    "太初剑宗": "Thái Sơ Kiếm Tông",
    "砚溪山庄": "Nghiên Khê Sơn Trang",
    "神兵山庄": "Thần Binh Sơn Trang",
    "山庄": "Sơn Trang",
    "红玉楼": "Hồng Ngọc Lâu",
    "昭月宗": "Chiêu Nguyệt Tông",
    "丐门": "Cái Môn",
    "大雪山": "Đại Tuyết Sơn",
    "嵩室山": "Tung Thất Sơn",
    "雁州": "Nhạn Châu",
    "仙洲": "Tiên Châu",
    "公输": "Công Thâu",
    "独孤": "Độc Cô",
    "禅师": "Thiền Sư",
    "宗师": "Tông Sư",
    "扫地僧": "Tăng Quét Sân",
    "千机峰": "Thiên Cơ Phong",
    "遥雪峰": "Diêu Tuyết Phong",
    "荒坨岭": "Hoang Đà Lĩnh",
    "珈南遗迹": "Già Nam Di Tích",
    "尧光谷": "Nghiêu Quang Cốc",
    "小石镇": "Tiểu Thạch Trấn",
    "冼星堂": "Tiển Tinh Đường",
    "不夜京": "Bất Dạ Kinh",
    "无涯海": "Vô Nhai Hải",
    "胜云山": "Thắng Vân Sơn",
    "老君山": "Lão Quân Sơn",
    "云鹤": "Vân Hạc",
    "清玉门": "Thanh Ngọc Môn",
    "信陵宗": "Tín Lăng Tông",
    "朔海派": "Sóc Hải Phái",
    "东厂": "Đông Xưởng",
    "双修府": "Song Tu Phủ",
    "鸿月帮": "Hồng Nguyệt Bang",
    "枰栌": "Bình Lô",
    "NPC": "NPC",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--section", required=True)
    parser.add_argument("--field-index", type=int)
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    return parser.parse_args()


def database_url() -> str:
    config = Path(".codex/config.toml").read_text(encoding="utf-8")
    match = re.search(r'^DATABASE_URI = "(.*)"', config, re.M)
    if not match:
        raise SystemExit("Missing database URL")
    return match.group(1)


def load_char_map() -> dict[str, str]:
    path = Path("_viethoa/chinese-hanviet-cognates/inputs/thieuchuu.txt")
    char_map: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        if "=" not in raw or raw.startswith("\t"):
            continue
        char, rest = raw.split("=", 1)
        reading = rest.split("[", 1)[0].strip().split(",", 1)[0].strip()
        if len(char) == 1 and reading:
            char_map[char] = unicodedata.normalize(
                "NFC", reading.replace("Ð", "Đ").replace("ð", "đ")
            )
    char_map.update(EXTRA_CHAR_MAP)
    return char_map


def cap(word: str) -> str:
    return word[:1].upper() + word[1:]


def hanviet(text: str, char_map: dict[str, str]) -> str:
    text = unicodedata.normalize("NFC", text).replace("丶", "")
    for source, translated in sorted(PHRASE_MAP.items(), key=lambda item: len(item[0]), reverse=True):
        text = text.replace(source, f" {translated} ")

    parts: list[str] = []
    buffer: list[str] = []
    for char in text:
        if "\u4e00" <= char <= "\u9fff":
            buffer.append(char)
            continue
        if buffer:
            parts.append(" ".join(cap(char_map.get(item, item)) for item in buffer))
            buffer.clear()
        parts.append(char)
    if buffer:
        parts.append(" ".join(cap(char_map.get(item, item)) for item in buffer))

    output = "".join(parts)
    output = re.sub(r"\s+", " ", output).strip()
    output = re.sub(r"\s*([(（])\s*", r" \1", output)
    output = re.sub(r"\s+([)）])", r"\1", output)
    output = re.sub(r"\s*·\s*", r"·", output)
    output = re.sub(r"\s*：\s*", r"：", output)
    return output


def main() -> int:
    args = parse_args()
    char_map = load_char_map()
    db_url = args.database_url or database_url()
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
              and ds.name = %s
              and (%s::integer is null or df.field_index = %s)
            order by tv.id
            limit %s
            """,
            (args.section, args.field_index, args.field_index, args.limit),
        )
        rows = cur.fetchall()
        updates = [(row_id, hanviet(source, char_map)) for row_id, source in rows]
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
        print(f"attempted {len(updates)}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
