
from __future__ import annotations

import re
from pathlib import Path

_CHINESE_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}


def chinese_number_to_int(text: str) -> int | None:
    """Parse common Chinese episode numbers from 一 to 九百九十九."""
    text = text.strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)

    total = 0
    section = 0
    number = 0
    saw_unit = False
    saw_digit = False

    for char in text:
        if char in _CHINESE_DIGITS:
            number = _CHINESE_DIGITS[char]
            saw_digit = True
        elif char == "十":
            saw_unit = True
            if number == 0:
                number = 1
            section += number * 10
            number = 0
        elif char == "百":
            saw_unit = True
            if number == 0:
                number = 1
            section += number * 100
            number = 0
        elif char == "千":
            saw_unit = True
            if number == 0:
                number = 1
            section += number * 1000
            number = 0
        else:
            return None

    total = section + number
    if saw_digit or saw_unit:
        return total
    return None


# 清晰度 / 编码 / 帧率 / 版本等“非集数”噪声：松散兜底前先剔除，避免把 1080P、x264 等当成集数。
_NOISE_RE = re.compile(
    r"(?i)("
    r"\d{3,4}\s*[pi]\b"                  # 1080p 720i 480p
    r"|\d+\s*fps"                        # 60fps
    r"|\d+\s*帧"                         # 60帧（中文帧率）
    r"|\d{2,4}\s*x\s*\d{2,4}"            # 1920x1080
    r"|(?<!\d)(?:480|540|576|720)(?!\d)"  # 3 位清晰度（1080/2160 等 4 位的会被“1~3 位”规则天然排除）
    r"|(?<!\d)[1-9]\s*[kK]\b"            # 2k 4k 8k（清晰度）
    r"|x26[45]|h\.?26[45]|hevc|av1"      # 编码
    r"|hdr10?|10\s*bit"
    r"|web-?dl|web-?rip|blu-?ray|remux"
    r"|\bv\d+\b"                         # v2 版本标记
    r")"
)


def parse_episode_number(filename: str) -> int | None:
    """从文件名识别集数。

    优先级：纯数字（1、01）> 第1集 / 第一集 / 1集 等带“集”标记 >
    松散兜底：剔除清晰度/编码等噪声后，取剩下最后一个 1~3 位独立数字（如 超清-6、EP6、S01E06）。
    """
    stem = Path(filename).stem.strip()
    if re.fullmatch(r"\d{1,5}", stem):
        return int(stem)

    patterns = [
        r"第\s*(\d{1,5})\s*集",
        r"第\s*([零〇一二两三四五六七八九十百千]+)\s*集",
        r"(?<!\d)(\d{1,5})\s*集",
        r"([零〇一二两三四五六七八九十百千]+)\s*集",
    ]
    for pattern in patterns:
        match = re.search(pattern, filename)
        if not match:
            continue
        raw = match.group(1)
        if raw.isdigit():
            return int(raw)
        parsed = chinese_number_to_int(raw)
        if parsed is not None:
            return parsed

    # 松散兜底：没有“集/话”标记时（超清-6、EP6、S01E06、1080P-6），
    # 去掉清晰度/编码噪声后取最后一个 1~3 位独立数字。
    cleaned = _NOISE_RE.sub(" ", stem)
    loose = re.findall(r"(?<!\d)\d{1,3}(?!\d)", cleaned)
    if loose:
        return int(loose[-1])
    return None
