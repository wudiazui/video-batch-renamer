
import sys
import unittest
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from renamer import (
    RenameOptions,
    apply_manual_episode,
    build_rename_plan,
    build_rename_plans,
    clear_windows_hidden_attribute,
    example_names,
    execute_rename_plan,
    get_windows_file_attributes,
    make_staging_path,
    set_windows_file_attributes,
)
from scanner import find_videos


VIDEO_BYTES = b"fake video data"


def touch(path: Path, data: bytes = VIDEO_BYTES):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


class RenamerTests(unittest.TestCase):
    def test_sequential_mode_moves_nested_videos_and_deletes_only_empty_folders(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original_empty_dir = root / "already_empty"
            original_empty_dir.mkdir()
            touch(root / "b" / "02.mp4")
            touch(root / "a" / "01.mov")
            keep_dir = root / "keep"
            touch(keep_dir / "03.mkv")
            (keep_dir / "note.txt").write_text("keep me", encoding="utf-8")

            plan = build_rename_plan(
                root,
                RenameOptions(mode="sequential", start_number=40),
            )

            self.assertTrue(plan.can_execute)
            self.assertEqual([item.new_path.name for item in plan.items], ["40.mov", "41.mp4", "42.mkv"])

            result = execute_rename_plan(plan)

            self.assertTrue(result.success)
            self.assertTrue((root / "40.mov").exists())
            self.assertTrue((root / "41.mp4").exists())
            self.assertTrue((root / "42.mkv").exists())
            self.assertFalse((root / "a").exists(), "empty folder should be deleted")
            self.assertFalse((root / "b").exists(), "empty folder should be deleted")
            self.assertTrue((keep_dir / "note.txt").exists(), "non-video file should be preserved")
            self.assertTrue(keep_dir.exists(), "folder containing non-video file should remain")
            self.assertTrue(original_empty_dir.exists(), "unrelated empty folder should remain")
            self.assertIsNotNone(result.log_path)
            self.assertTrue(result.log_path.exists())

    def test_episode_mode_orders_by_detected_episode_and_uses_title_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            touch(root / "第十集.mp4")
            touch(root / "nested" / "短剧-第2集.mov")
            touch(root / "第一集.mkv")

            plan = build_rename_plan(
                root,
                RenameOptions(mode="episode", episode_output="title", title="我的短剧"),
            )

            self.assertTrue(plan.can_execute)
            self.assertEqual(
                [item.new_path.name for item in plan.items],
                ["我的短剧-第1集.mkv", "我的短剧-第2集.mov", "我的短剧-第10集.mp4"],
            )

    def test_episode_mode_supports_padding_and_template(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            touch(root / "1.mp4")
            touch(root / "02.mp4")

            plan = build_rename_plan(
                root,
                RenameOptions(mode="episode", title="短剧", template="{title}-EP{episode}", number_width=3),
            )

            self.assertTrue(plan.can_execute)
            self.assertEqual([item.new_path.name for item in plan.items], ["短剧-EP001.mp4", "短剧-EP002.mp4"])

    def test_sequential_mode_supports_padding_template_and_original_extension_case(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            touch(root / "A.MP4")

            plan = build_rename_plan(
                root,
                RenameOptions(
                    mode="sequential",
                    start_number=7,
                    template="clip-{number}",
                    number_width=2,
                    keep_extension_case=True,
                ),
            )

            self.assertTrue(plan.can_execute)
            self.assertEqual(plan.items[0].new_path.name, "clip-07.MP4")

    def test_episode_mode_can_output_episode_only_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            touch(root / "片名-第一集.mp4")

            plan = build_rename_plan(
                root,
                RenameOptions(mode="episode", episode_output="episode_only"),
            )

            self.assertTrue(plan.can_execute)
            self.assertEqual(plan.items[0].new_path.name, "第1集.mp4")

    def test_episode_mode_marks_unrecognized_files_as_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            touch(root / "花絮.mp4")

            plan = build_rename_plan(root, RenameOptions(mode="episode"))

            self.assertFalse(plan.can_execute)
            self.assertIn("无法识别集数", plan.items[0].status)

    def test_invalid_title_marks_plan_as_not_executable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            touch(root / "第1集.mp4")

            plan = build_rename_plan(
                root,
                RenameOptions(mode="episode", episode_output="title", title="坏/标题"),
            )

            self.assertFalse(plan.can_execute)
            self.assertTrue(any("非法文件名字符" in error for error in plan.errors))

    def test_duplicate_generated_target_prevents_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            touch(root / "上" / "第1集.mp4")
            touch(root / "下" / "第一集.mp4")

            plan = build_rename_plan(
                root,
                RenameOptions(mode="episode", episode_output="episode_only"),
            )

            self.assertFalse(plan.can_execute)
            self.assertTrue(any("目标文件名重复" in item.status for item in plan.items))

    def test_execute_rechecks_targets_and_does_not_overwrite_late_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = touch(root / "nested" / "01.mp4", b"original")

            plan = build_rename_plan(root, RenameOptions(mode="sequential", start_number=40))
            self.assertTrue(plan.can_execute)

            late_target = touch(root / "40.mp4", b"late conflict")
            result = execute_rename_plan(plan)

            self.assertFalse(result.success)
            self.assertEqual(late_target.read_bytes(), b"late conflict")
            self.assertTrue(original.exists(), "original file should remain after failed execution")
            self.assertIsNotNone(result.log_path)
            self.assertTrue(result.log_path.exists())


    def test_staging_path_does_not_start_with_dot_to_avoid_smb_hidden_attribute(self):
        original = Path(r"Z:\素材\第1集.mp4")

        staging_path = make_staging_path(original, token="abc", index=0)

        self.assertFalse(staging_path.name.startswith("."))
        self.assertIn("renaming", staging_path.name)
        self.assertEqual(staging_path.parent, original.parent)


    @unittest.skipUnless(sys.platform.startswith("win"), "Windows file attributes only")
    def test_clear_windows_hidden_attribute_removes_hidden_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = touch(Path(tmp) / "hidden.mp4")
            original_attrs = get_windows_file_attributes(path)
            self.assertIsNotNone(original_attrs)
            set_windows_file_attributes(path, original_attrs | 0x2)

            clear_windows_hidden_attribute(path)

            updated_attrs = get_windows_file_attributes(path)
            self.assertIsNotNone(updated_attrs)
            self.assertFalse(updated_attrs & 0x2)

    def test_reserved_windows_name_blocks_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            touch(root / "clip.mp4")

            plan = build_rename_plan(
                root,
                RenameOptions(mode="sequential", start_number=1, template="CON"),
            )

            self.assertFalse(plan.can_execute)
            self.assertTrue(any(item.error_type == "reserved_name" for item in plan.items))

    def test_build_rename_plans_continuous_numbering_across_folders(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "a"
            second = Path(tmp) / "b"
            touch(first / "x1.mp4")
            touch(first / "x2.mp4")
            touch(first / "x3.mp4")
            touch(second / "y1.mp4")
            touch(second / "y2.mp4")

            plans = build_rename_plans(
                [first, second],
                RenameOptions(mode="sequential", start_number=1),
                continuous_numbering=True,
            )

            self.assertEqual([item.new_path.name for item in plans[0].items], ["1.mp4", "2.mp4", "3.mp4"])
            self.assertEqual([item.new_path.name for item in plans[1].items], ["4.mp4", "5.mp4"])

    def test_build_rename_plans_independent_numbering_restarts_per_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "a"
            second = Path(tmp) / "b"
            touch(first / "x1.mp4")
            touch(first / "x2.mp4")
            touch(second / "y1.mp4")

            plans = build_rename_plans(
                [first, second],
                RenameOptions(mode="sequential", start_number=1),
                continuous_numbering=False,
            )

            self.assertEqual([item.new_path.name for item in plans[0].items], ["1.mp4", "2.mp4"])
            self.assertEqual([item.new_path.name for item in plans[1].items], ["1.mp4"])

    def test_example_names_sequential(self):
        names = example_names(RenameOptions(mode="sequential", start_number=1))
        self.assertEqual(names, ["1.mp4", "2.mp4", "3.mp4"])

    def test_example_names_sequential_padding_and_template(self):
        names = example_names(RenameOptions(mode="sequential", start_number=1, template="EP{number}", number_width=2))
        self.assertEqual(names, ["EP01.mp4", "EP02.mp4", "EP03.mp4"])

    def test_example_names_episode_with_title(self):
        names = example_names(RenameOptions(mode="episode", episode_output="title", title="剧名"))
        self.assertEqual(names, ["剧名-第1集.mp4", "剧名-第2集.mp4", "剧名-第3集.mp4"])

    def test_apply_manual_episode_fixes_unrecognized_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            touch(root / "第1集.mp4")
            touch(root / "花絮.mp4")  # 自动识别不出

            plan = build_rename_plan(root, RenameOptions(mode="episode"))
            self.assertFalse(plan.can_execute)
            unrecognized = next(item for item in plan.items if item.old_path.name == "花絮.mp4")
            self.assertIsNone(unrecognized.episode_number)

            apply_manual_episode(plan, unrecognized, 2)

            self.assertEqual(unrecognized.episode_number, 2)
            self.assertEqual(unrecognized.new_path.name, "第2集.mp4")
            self.assertTrue(unrecognized.ok)
            self.assertTrue(plan.can_execute)

    def test_apply_manual_episode_detects_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            touch(root / "第1集.mp4")
            touch(root / "花絮.mp4")

            plan = build_rename_plan(root, RenameOptions(mode="episode"))
            unrecognized = next(item for item in plan.items if item.old_path.name == "花絮.mp4")
            apply_manual_episode(plan, unrecognized, 1)  # 故意撞上已有的第1集

            self.assertFalse(plan.can_execute)
            self.assertTrue(any("重复" in item.status for item in plan.items))

    def test_find_videos_recurses_and_ignores_non_video_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            touch(root / "a" / "clip.mp4")
            (root / "a" / "note.txt").write_text("not video", encoding="utf-8")

            videos = find_videos(root)

            self.assertEqual([p.name for p in videos], ["clip.mp4"])


if __name__ == "__main__":
    unittest.main()
