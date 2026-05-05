#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path

import psycopg2
from psycopg2.extras import execute_values

from apply_hanviet_name_batch import HAN_RE, database_url, hanviet, load_char_map


DEFAULT_SECTION = "condition_group"
DEFAULT_FIELD_INDEX = 17
DEFAULT_SOURCE_REGEX = (
    r"^(完成|与|和|击败|击杀|追上|告知|靠近|阅读|到达|前往|回去找|使用|"
    r"小心|采集|打开|寻找|找到|去找|去|向|听|拿|取得|获得|交给|将|"
    r"归还|交还|赠给|取回|夺回|购买|制作|陪|护送|带走|救出|救治|治疗)"
)


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def hv_name(text: str, char_map: dict[str, str]) -> str:
    return hanviet(text, char_map)


def objective(source: str, char_map: dict[str, str]) -> str:
    s = source.strip()
    hv = lambda value: hv_name(value.strip(), char_map)

    fixed = {
        "推理真相": "Suy luận chân tướng",
        "静观其变": "Tĩnh quan kỳ biến",
        "做出自己的选择": "Đưa ra lựa chọn của mình",
        "不要距离蒋思聪过远": "Đừng cách Tưởng Tư Thông quá xa",
        "离开这里": "Rời khỏi đây",
        "达到墨者水平": "Đạt trình độ Mặc Giả",
        "达到墨使水平": "Đạt trình độ Mặc Sứ",
        "修复日晷": "Tu sửa nhật quỹ",
        "打开四象阵": "Mở Tứ Tượng Trận",
        "打开宝藏": "Mở kho báu",
        "使用钥匙": "Dùng chìa khóa",
        "小心敌人": "Cẩn thận kẻ địch",
        "到达目的地": "Đến địa điểm mục tiêu",
        "搞清状况": "Làm rõ tình hình",
        "躲在一旁": "Nấp sang một bên",
        "联系旧部": "Liên lạc bộ hạ cũ",
        "阻止内讧": "Ngăn nội loạn",
        "前往坠龙坑历练": "Đến Trụy Long Khanh lịch luyện",
        "交付冰湖莼菜": "Giao Băng Hồ Thuần Thái",
        "击败值守士兵": "Đánh bại lính canh",
        "给老人捡东西": "Nhặt đồ giúp lão nhân",
        "溜走": "Lẻn đi",
        "偷听他们在说什么": "Nghe lén họ đang nói gì",
        "甩掉跟踪者": "Cắt đuôi kẻ bám theo",
        "砸开石门进入内部": "Phá cửa đá để vào trong",
        "告诉凌云门你们的决定": "Báo quyết định của các ngươi cho Lăng Vân Môn",
        "查看林芝给你的青锋剑": "Xem Thanh Phong Kiếm Lâm Chi đưa cho ngươi",
        "告诉谷夫人你的推测": "Báo suy đoán của ngươi cho Cốc phu nhân",
        "瞻拜菩提像": "Chiêm bái tượng Bồ Đề",
        "替众人解毒": "Giải độc cho mọi người",
        "静观其变": "Tĩnh quan kỳ biến",
    }
    if s in fixed:
        return fixed[s]

    patterns: list[tuple[str, str]] = [
        (r"^去找(.+)$", "Đi tìm {0}"),
        (r"^去(.+)瞧瞧$", "Đến {0} xem thử"),
        (r"^去(.+)$", "Đến {0}"),
        (r"^前往(.+)请教(.+)$", "Đến {0} thỉnh giáo {1}"),
        (r"^前往(.+)内部$", "Đến trong {0}"),
        (r"^前去(.+)处请教(.+)$", "Đến {0} thỉnh giáo {1}"),
        (r"^于(.+)处请教(.+)$", "Thỉnh giáo {1} tại {0}"),
        (r"^前往(.+)$", "Đến {0}"),
        (r"^进入(.+)内部$", "Vào trong {0}"),
        (r"^回去找(.+)$", "Quay lại tìm {0}"),
        (r"^来到(.+)$", "Đến {0}"),
        (r"^靠近(.+)$", "Lại gần {0}"),
        (r"^阅读(.+)$", "Đọc {0}"),
        (r"^告知(.+)$", "Báo cho {0}"),
        (r"^完成奇遇任务《(.+)》$", "Hoàn thành nhiệm vụ kỳ ngộ {0}"),
        (r"^在(.+)寻找(.+)$", "Tìm {1} ở {0}"),
        (r"^(.+)处寻(.+)$", "Tìm {1} tại {0}"),
        (r"^寻找(.+)$", "Tìm {0}"),
        (r"^寻(.+)$", "Tìm {0}"),
        (r"^找到(.+)的消息$", "Tìm tin tức về {0}"),
        (r"^找到(.+)后完成推理$", "Tìm {0} rồi hoàn thành suy luận"),
        (r"^找到(.+)$", "Tìm thấy {0}"),
        (r"^找(.+)了解详情$", "Tìm {0} hỏi rõ chi tiết"),
        (r"^找(.+)问问$", "Tìm {0} hỏi thử"),
        (r"^找(.+)$", "Tìm {0}"),
        (r"^击败(.+)和(.+)$", "Đánh bại {0} và {1}"),
        (r"^击败(.+)与(.+)$", "Đánh bại {0} và {1}"),
        (r"^击败全部(.+)$", "Đánh bại toàn bộ {0}"),
        (r"^击败(.+)等人$", "Đánh bại {0} và những người khác"),
        (r"^击败(.+)二人$", "Đánh bại hai người {0}"),
        (r"^击败(.+)$", "Đánh bại {0}"),
        (r"^击杀(.+)$", "Giết {0}"),
        (r"^打倒(.+)和其小弟$", "Đánh bại {0} và đàn em"),
        (r"^尽力抵抗(.+)的攻击$", "Dốc sức chống đỡ đòn tấn công của {0}"),
        (r"^如有可能竭力击败(.+)$", "Nếu có thể, dốc sức đánh bại {0}"),
        (r"^切磋击败(.+)$", "Luận võ đánh bại {0}"),
        (r"^与(.+)交谈$", "Trò chuyện với {0}"),
        (r"^和(.+)交谈$", "Trò chuyện với {0}"),
        (r"^与(.+)交流$", "Trao đổi với {0}"),
        (r"^与(.+)闲聊$", "Tán gẫu với {0}"),
        (r"^与(.+)喝酒$", "Uống rượu với {0}"),
        (r"^与(.+)对话$", "Đối thoại với {0}"),
        (r"^再次和她对话$", "Lại đối thoại với cô ấy"),
        (r"^与(.+)对弈并取胜$", "Đánh cờ với {0} và giành thắng lợi"),
        (r"^与(.+)回家$", "Về nhà cùng {0}"),
        (r"^向(.+)打听$", "Hỏi thăm {0}"),
        (r"^向(.+)了解详情$", "Hỏi {0} để hiểu rõ chi tiết"),
        (r"^向(.+)询问详情$", "Hỏi {0} về chi tiết"),
        (r"^询问(.+)的消息$", "Hỏi tin tức về {0}"),
        (r"^听(.+)交谈$", "Nghe {0} trò chuyện"),
        (r"^听(.+)谈话$", "Nghe {0} trò chuyện"),
        (r"^听(.+)说话$", "Nghe {0} nói chuyện"),
        (r"^听(.+)讲明事情$", "Nghe {0} kể rõ sự việc"),
        (r"^听一听发生了什么$", "Nghe xem đã xảy ra chuyện gì"),
        (r"^看看(.+)藏着什么$", "Xem {0} giấu thứ gì"),
        (r"^问问(.+)有什么事情$", "Hỏi {0} có chuyện gì"),
        (r"^瞧瞧(.+)$", "Xem thử {0}"),
        (r"^将(.+)交给(.+)$", "Giao {0} cho {1}"),
        (r"^交给(.+?)(.+)$", "Giao {1} cho {0}"),
        (r"^归还(.+?)(.+)$", "Trả {1} cho {0}"),
        (r"^交还(.+?)(.+)$", "Trả {1} cho {0}"),
        (r"^赠给(.+?)(.+)$", "Tặng {1} cho {0}"),
        (r"^施给(.+)$", "Bố thí cho {0}"),
        (r"^取回(.+)$", "Lấy lại {0}"),
        (r"^夺回(.+)$", "Đoạt lại {0}"),
        (r"^拿取(.+)$", "Lấy {0}"),
        (r"^取得(.+)$", "Lấy được {0}"),
        (r"^拿到(.+)$", "Lấy được {0}"),
        (r"^得到(.+)$", "Nhận được {0}"),
        (r"^获得(.+)$", "Nhận được {0}"),
        (r"^凑齐(.+)$", "Thu thập đủ {0}"),
        (r"^采集(.+)$", "Thu thập {0}"),
        (r"^购买(.+)$", "Mua {0}"),
        (r"^花(.+)购买(.+)$", "Tốn {0} để mua {1}"),
        (r"^制作(.+)给(.+)品尝$", "Làm {0} cho {1} nếm thử"),
        (r"^陪(.+)玩(.+)$", "Chơi {1} cùng {0}"),
        (r"^陪(.+)玩$", "Chơi cùng {0}"),
        (r"^追上(.+)$", "Đuổi kịp {0}"),
        (r"^护送(.+)到达(.+)$", "Hộ tống {0} đến {1}"),
        (r"^带走(.+)$", "Đưa {0} đi"),
        (r"^救出(.+)$", "Cứu {0}"),
        (r"^救治(.+)$", "Cứu chữa {0}"),
        (r"^治疗(.+)$", "Trị liệu {0}"),
        (r"^开导(.+)$", "Khuyên giải {0}"),
        (r"^劝诫(.+)$", "Khuyên răn {0}"),
        (r"^劝(.+)离开$", "Khuyên {0} rời đi"),
        (r"^完成(.+)的悬赏$", "Hoàn thành treo thưởng của {0}"),
        (r"^完成(.+)$", "Hoàn thành {0}"),
        (r"^帮忙处理(.+)$", "Giúp xử lý {0}"),
        (r"^替(.+)觅得(.+)$", "Tìm {1} giúp {0}"),
        (r"^替(.+)解毒$", "Giải độc cho {0}"),
        (r"^看(.+)展示(.+)$", "Xem {0} biểu diễn {1}"),
        (r"^等待(.+)$", "Chờ {0}"),
        (r"^来到(.+)打开(.+)$", "Đến {0} mở {1}"),
    ]

    for pattern, template in patterns:
        match = re.match(pattern, s)
        if not match:
            continue
        groups = [hv(item) for item in match.groups()]
        return clean(template.format(*groups))

    return hv(s)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--section", default=DEFAULT_SECTION)
    parser.add_argument("--field-index", type=int, default=DEFAULT_FIELD_INDEX)
    parser.add_argument("--source-regex", default=None)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()

    char_map = load_char_map()
    with psycopg2.connect(database_url()) as conn, conn.cursor() as cur:
        filters = ["tv.status = 'pending'", "ds.name = %s"]
        params: list[object] = [args.section]
        if args.field_index is not None:
            filters.append("df.field_index = %s")
            params.append(args.field_index)
        source_regex = args.source_regex
        if source_regex is None and args.section != DEFAULT_SECTION:
            source_regex = DEFAULT_SOURCE_REGEX
        if source_regex:
            filters.append("tv.source_text ~ %s")
            params.append(source_regex)
        limit_sql = ""
        if args.limit is not None:
            limit_sql = "limit %s"
            params.append(args.limit)
        cur.execute(
            f"""
            select distinct on (tv.id) tv.id, tv.source_text
            from translation_values tv
            join translation_occurrences toc on toc.translation_value_id = tv.id
            join db_fields df on df.id = toc.db_field_id
            join db_lines dl on dl.id = df.line_id
            join db_sections ds on ds.id = dl.section_id
            where {" and ".join(filters)}
            order by tv.id
            {limit_sql}
            """,
            params,
        )
        rows = cur.fetchall()
        updates = [(row_id, objective(source, char_map)) for row_id, source in rows]
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
