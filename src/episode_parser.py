
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


def parse_episode_number(filename: str) -> int | None:
    """Extract episode number from names like 第1集, 第一集, 1集, 一集."""
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
    return None
