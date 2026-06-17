import csv
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from operation_log import build_undo_plan, execute_undo_plan
from renamer import RenameOptions, build_rename_plan, execute_rename_plan


def touch(path: Path, data: bytes = b"fake video data"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


class OperationLogTests(unittest.TestCase):
    def test_successful_execution_writes_csv_log_and_undo_can_restore(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = touch(root / "nested" / "第1集.mp4")

            plan = build_rename_plan(root, RenameOptions(mode="episode"))
            result = execute_rename_plan(plan)

            self.assertTrue(result.success)
            self.assertTrue(result.log_path.exists())
            with result.log_path.open("r", encoding="utf-8-sig", newline="") as file:
                rows = list(csv.DictReader(file))
            self.assertEqual(rows[0]["original_path"], str(original))
            self.assertEqual(rows[0]["new_path"], str(root / "第1集.mp4"))
            self.assertEqual(rows[0]["status"], "success")

            undo_plan = build_undo_plan(result.log_path)
            self.assertTrue(undo_plan.can_execute)
            undo_result = execute_undo_plan(undo_plan)

            self.assertTrue(undo_result.success)
            self.assertTrue(original.exists())
            self.assertFalse((root / "第1集.mp4").exists())

    def test_undo_plan_rejects_target_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = touch(root / "nested" / "第1集.mp4")

            plan = build_rename_plan(root, RenameOptions(mode="episode"))
            result = execute_rename_plan(plan)
            self.assertTrue(result.success)
            touch(original, b"conflict")

            undo_plan = build_undo_plan(result.log_path)

            self.assertFalse(undo_plan.can_execute)
            self.assertTrue(any("目标已存在" in item.status for item in undo_plan.items))

    def test_multiple_folder_executions_write_separate_logs(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "first"
            second = Path(tmp) / "second"
            touch(first / "a" / "1.mp4")
            touch(second / "b" / "1.mp4")

            first_result = execute_rename_plan(build_rename_plan(first, RenameOptions(mode="episode")))
            second_result = execute_rename_plan(build_rename_plan(second, RenameOptions(mode="episode")))

            self.assertTrue(first_result.success)
            self.assertTrue(second_result.success)
            self.assertTrue(first_result.log_path.exists())
            self.assertTrue(second_result.log_path.exists())
            self.assertNotEqual(first_result.log_path.parent, second_result.log_path.parent)

    def test_move_no_overwrite_moves_then_refuses_existing_target(self):
        from operation_log import move_no_overwrite

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = touch(root / "a.mp4", b"original")
            target = root / "sub" / "b.mp4"

            move_no_overwrite(source, target)
            self.assertTrue(target.exists())
            self.assertFalse(source.exists())
            self.assertEqual(target.read_bytes(), b"original")

            other = touch(root / "c.mp4", b"other")
            with self.assertRaises(FileExistsError):
                move_no_overwrite(other, target)
            self.assertEqual(target.read_bytes(), b"original", "已存在的目标不能被覆盖")
            self.assertTrue(other.exists(), "拒绝移动后源文件应保留")


if __name__ == "__main__":
    unittest.main()
