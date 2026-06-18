
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from episode_parser import parse_episode_number


class EpisodeParserTests(unittest.TestCase):
    def test_parses_arabic_and_chinese_episode_numbers(self):
        examples = {
            "短剧-第1集.mp4": 1,
            "短剧 第01集.mp4": 1,
            "精彩内容_1集.mp4": 1,
            "第一集 开场.mp4": 1,
            "一集.mp4": 1,
            "第十集.mp4": 10,
            "第二十三集.mp4": 23,
            "第100集.mp4": 100,
        }
        for name, expected in examples.items():
            with self.subTest(name=name):
                self.assertEqual(parse_episode_number(name), expected)

    def test_returns_none_when_no_episode_number_exists(self):
        self.assertIsNone(parse_episode_number("花絮.mp4"))
        self.assertIsNone(parse_episode_number("第集.mp4"))
        self.assertIsNone(parse_episode_number("1080P.mp4"))      # 纯清晰度不是集数
        self.assertIsNone(parse_episode_number("我的剧2024.mp4"))  # 四位年份不当集数
        self.assertIsNone(parse_episode_number("1920x1080.mp4"))  # 分辨率不当集数
        self.assertIsNone(parse_episode_number("2k.mp4"))         # 纯 2k 不是集数

    def test_parses_loosely_embedded_numbers_without_marker(self):
        # 没有“集/话”标记，但带数字：剔除清晰度/编码噪声后取最后一个 1~3 位独立数字。
        examples = {
            "超清-6.mp4": 6,
            "EP6.mp4": 6,
            "第6话.mp4": 6,
            "S01E06.mp4": 6,
            "1080P-6.mp4": 6,
            "超清720-8.mp4": 8,
            "x264-12.mp4": 12,
            "超清-2k-补帧-7.mp4": 7,
            "7-超清-2k.mp4": 7,
            "蓝光4K原盘-12.mp4": 12,
            "3-超清-60帧.mp4": 3,
        }
        for name, expected in examples.items():
            with self.subTest(name=name):
                self.assertEqual(parse_episode_number(name), expected)

    def test_parses_pure_numeric_stem_as_episode_number(self):
        self.assertEqual(parse_episode_number("1.mp4"), 1)
        self.assertEqual(parse_episode_number("02.mov"), 2)
        self.assertEqual(parse_episode_number("003.mkv"), 3)

    def test_parses_chinese_tens_and_hundreds(self):
        examples = {
            "第十一集.mp4": 11,
            "十二集.mp4": 12,
            "第二十集.mp4": 20,
            "九十九集.mp4": 99,
            "第一百零八集.mp4": 108,
        }
        for name, expected in examples.items():
            with self.subTest(name=name):
                self.assertEqual(parse_episode_number(name), expected)


if __name__ == "__main__":
    unittest.main()
