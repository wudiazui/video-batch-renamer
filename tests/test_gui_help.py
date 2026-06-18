import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from main import HELP_TEXT, VideoRenamerApp


class GuiHelpTests(unittest.TestCase):
    def test_help_text_covers_first_time_user_flow(self):
        required_phrases = [
            "添加文件夹",
            "生成预览",
            "确认执行改名",
            "{title}-第{episode}集",
            "跨文件夹连续编号",
            "_rename_logs",
            "撤销",
        ]
        for phrase in required_phrases:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, HELP_TEXT)

    def test_main_window_has_core_buttons(self):
        app = VideoRenamerApp()
        app.withdraw()
        try:
            button_texts = []

            def collect_button_texts(widget):
                for child in widget.winfo_children():
                    if child.winfo_class() == "TButton":
                        button_texts.append(child.cget("text"))
                    collect_button_texts(child)

            collect_button_texts(app)
            self.assertIn("添加文件夹", button_texts)
            self.assertIn("生成预览", button_texts)
            self.assertNotIn("使用说明", button_texts)  # 帮助按钮已移除
        finally:
            app.destroy()


class GuiInteractionTests(unittest.TestCase):
    def _make_app(self) -> VideoRenamerApp:
        app = VideoRenamerApp()
        app.withdraw()
        # 不依赖磁盘上已保存的历史文件夹。
        app.folders.clear()
        app._folder_counts.clear()
        app._refresh_folder_list()
        return app

    def test_folder_list_is_single_source_of_truth(self):
        app = self._make_app()
        try:
            self.assertTrue(app._append_folder(r"Z:\剧A", count=3))
            self.assertFalse(app._append_folder(r"Z:\剧A", count=5), "重复文件夹应被忽略")
            app._refresh_folder_list()
            self.assertEqual(app._folders_for_action(), [str(Path(r"Z:\剧A"))])
            self.assertEqual(app.folder_listbox.size(), 1)
        finally:
            app.destroy()

    def test_cross_folder_checkbox_disabled_in_episode_mode(self):
        app = self._make_app()
        try:
            app.mode.set("sequential")
            app._on_mode_changed()
            self.assertEqual(str(app.cross_folder_check.cget("state")), "normal")
            app.mode.set("episode")
            app._on_mode_changed()
            self.assertEqual(str(app.cross_folder_check.cget("state")), "disabled")
        finally:
            app.destroy()

    def test_mode_switch_preserves_per_mode_template(self):
        app = self._make_app()
        try:
            app.mode.set("sequential")
            app._on_mode_changed()
            app.template.set("EP{number}")
            app.mode.set("episode")
            app._on_mode_changed()
            self.assertNotEqual(app.template.get(), "EP{number}")
            app.mode.set("sequential")
            app._on_mode_changed()
            self.assertEqual(app.template.get(), "EP{number}")
        finally:
            app.destroy()

    def test_changing_setting_invalidates_preview(self):
        app = self._make_app()
        try:
            app._preview_valid = True
            app.execute_button.configure(state="normal")
            app.start_number.set("99")  # 触发 trace
            self.assertFalse(app._preview_valid)
            self.assertEqual(str(app.execute_button.cget("state")), "disabled")
        finally:
            app.destroy()

    def test_tooltip_show_and_hide_do_not_crash(self):
        from main import Tooltip

        app = self._make_app()
        try:
            app.update_idletasks()
            tip = Tooltip(app.cross_folder_check, "测试说明")
            tip._show()
            self.assertIsNotNone(tip._tip)
            tip._hide()
            self.assertIsNone(tip._tip)
        finally:
            app.destroy()

    def test_busy_blocks_second_execute(self):
        import main as main_module
        from unittest import mock

        app = self._make_app()
        try:
            app._preview_valid = True
            app._set_busy(True)  # 模拟一次执行正在进行
            with mock.patch.object(main_module.threading, "Thread") as thread_cls, mock.patch.object(
                main_module.messagebox, "askyesno", return_value=True
            ):
                app._execute()  # 此时按 Ctrl+Enter 触发
                thread_cls.assert_not_called()  # 忙碌时不应再启动第二个执行线程
        finally:
            app.destroy()

    def test_busy_blocks_setting_invalidation(self):
        app = self._make_app()
        try:
            app._preview_valid = True
            app._set_busy(True)
            app._invalidate_preview()  # 执行中改设置不应清空预览有效性、重置进度
            self.assertTrue(app._preview_valid)
            app._set_busy(False)
            app._invalidate_preview()  # 非忙时正常失效
            self.assertFalse(app._preview_valid)
        finally:
            app.destroy()

    def test_async_folder_count_updates_list(self):
        app = self._make_app()
        try:
            folder = str(Path(r"Z:\剧A"))
            app._append_folder(folder, count=None)
            app._refresh_folder_list()
            self.assertNotIn("个视频", app.folder_listbox.get(0))
            app._apply_folder_count((folder, 7))  # 后台统计结果回到主线程
            self.assertEqual(app._folder_counts[folder], 7)
            self.assertIn("7 个视频", app.folder_listbox.get(0))
            # 在统计返回前已被移除的文件夹，迟到的结果应被忽略
            app._apply_folder_count((str(Path(r"Z:\已删除")), 3))
            self.assertNotIn(str(Path(r"Z:\已删除")), app._folder_counts)
        finally:
            app.destroy()

    def test_preview_double_click_locates_file(self):
        import main as main_module
        import tempfile
        from unittest import mock

        app = self._make_app()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                (Path(tmp) / "01.mp4").write_bytes(b"v")
                app._append_folder(tmp, count=None)
                app.mode.set("sequential")
                app._on_mode_changed()
                app.template.set("{number}")
                app.start_number.set("1")
                app.number_width.set("1")
                with mock.patch.object(main_module, "save_settings"):
                    app._preview()

                children = app.tree.get_children()
                self.assertEqual(len(children), 1)
                pair = app._plan_item_for_tree_id(children[0])
                self.assertIsNotNone(pair)
                _plan, item = pair
                self.assertEqual(item.old_path.name, "01.mp4")

                with mock.patch.object(main_module.subprocess, "Popen") as popen, mock.patch.object(
                    main_module.os, "startfile", create=True
                ) as startfile:
                    app._reveal_path(item.old_path)
                    self.assertTrue(popen.called or startfile.called)
        finally:
            app.destroy()

    def test_advanced_hidden_by_default_and_example_filled(self):
        app = self._make_app()
        try:
            self.assertFalse(app._advanced_visible)  # 高级设置默认折叠
            self.assertTrue(app.example_text.get())  # 实时“改名后示例”非空
            app._toggle_advanced()
            self.assertTrue(app._advanced_visible)
            app._toggle_advanced()
            self.assertFalse(app._advanced_visible)
        finally:
            app.destroy()

    def test_only_errors_checkbox_hides_ok_rows(self):
        import main as main_module
        import tempfile
        from unittest import mock

        app = self._make_app()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                (Path(tmp) / "01.mp4").write_bytes(b"v")
                (Path(tmp) / "花絮.mp4").write_bytes(b"v")
                app._append_folder(tmp, count=None)
                app.mode.set("episode")
                app._on_mode_changed()
                with mock.patch.object(main_module, "save_settings"):
                    app._preview()
                total = len(app.tree.get_children())
                app.only_errors.set(True)
                app._render_plans()
                self.assertLess(len(app.tree.get_children()), total)  # 只看出错后行数变少
        finally:
            app.destroy()


if __name__ == "__main__":
    unittest.main()
