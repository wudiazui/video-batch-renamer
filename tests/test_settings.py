import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from settings_store import load_settings, save_settings


class SettingsTests(unittest.TestCase):
    def test_settings_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            settings = {
                "folder": r"Z:\素材",
                "mode": "episode",
                "start_number": "40",
                "title": "短剧",
                "number_width": "3",
                "template": "{title}-第{episode}集",
                "keep_extension_case": True,
            }

            save_settings(settings, path)
            loaded = load_settings(path)

            self.assertEqual(loaded, settings)

    def test_missing_settings_returns_empty_dict(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(load_settings(Path(tmp) / "missing.json"), {})


if __name__ == "__main__":
    unittest.main()
