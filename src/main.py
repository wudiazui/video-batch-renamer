from __future__ import annotations

import os
import queue
import re
import subprocess
import threading
import traceback
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from operation_log import build_undo_plan, execute_undo_plan, find_latest_log
from renamer import RenameOptions, RenamePlan, build_rename_plans, example_names, execute_rename_plan
from scanner import find_videos
from settings_store import load_settings, save_settings


# 每种模式可选的模板，切换模式时联动切换下拉项。
SEQUENTIAL_TEMPLATES = ["{number}", "EP{number}", "clip-{number}"]
EPISODE_TEMPLATES = ["第{episode}集", "{title}-第{episode}集", "EP{episode}"]
DEFAULT_TEMPLATE = {"sequential": "{number}", "episode": "第{episode}集"}

GEOMETRY_RE = re.compile(r"^\d+x\d+([+-]\d+[+-]\d+)?$")

# 现代配色（零依赖，基于 ttk 内置 clam 主题）：白卡片 + 蓝色强调 + 灰底。
COLOR_BG = "#eef1f6"        # 卡片之间的底色
COLOR_CARD = "#ffffff"      # 卡片白底
COLOR_ACCENT = "#2d6cdf"    # 主强调蓝
COLOR_ACCENT_DARK = "#2356b8"
COLOR_TEXT = "#1f2a44"
COLOR_MUTED = "#5b6472"
COLOR_BORDER = "#c8ccd4"
COLOR_FIELD = "#ffffff"     # 输入框 / 表格白底
COLOR_BTN = "#e7eaf0"       # 普通按钮底
COLOR_BTN_ACTIVE = "#d8deea"
COLOR_SEL = "#cfe0ff"       # 选中高亮
COLOR_ERROR = "#b00020"
COLOR_OK = "#1a7f37"
FONT_BASE = ("Microsoft YaHei UI", 10)
FONT_TITLE = ("Microsoft YaHei UI", 11, "bold")
FONT_HEAD = ("Microsoft YaHei UI", 10, "bold")

# 把机器化的错误状态翻译成用户能照着做的一句话提示。
STATUS_TIPS = {
    "parse": "有文件识别不出集数：改用“连续编号”模式，或确认文件名里有 01、第1集 这样的标记。",
    "duplicate_target": "有多个文件会重名：检查不同文件夹里是否存在相同的集数或编号。",
    "target_exists": "目标文件已存在：换一个起始数字或剧名，或先处理掉同名文件。",
    "invalid_filename": '剧名或模板里有非法字符（\\ / : * ? " < > |）或首尾空格，请修改。',
    "reserved_name": "生成的文件名是 Windows 系统保留名（如 CON、PRN、NUL），请修改剧名或模板。",
    "config": "命名设置有误：请检查起始数字、剧名和模板。",
}

HELP_TEXT = (
    "使用流程：1. 点“添加文件夹”，把一个或多个文件夹加入列表；2. 选择命名模式、补零和模板；"
    "3. 点“生成预览”，确认没有红色错误；4. 点“确认执行改名”。\n"
    "命名模板：连续编号用 {number}；短剧集数用 第{episode}集；带剧名用 {title}-第{episode}集；"
    "EP 格式用 EP{episode}。纯数字文件名如 01.mp4 会按第 1 集识别。\n"
    "多个文件夹：默认每个文件夹各自从“起始数字”开始编号；勾选“跨文件夹连续编号”后，多个文件夹会接续编号"
    "（文件仍各自留在原来的文件夹里，不会合并到一起）。\n"
    "快捷键：Ctrl+O 添加文件夹，Ctrl+P 生成预览，Ctrl+Enter 执行改名，Ctrl+Z 撤销最近一次，列表里按 Delete 移除选中。\n"
    "安全提示：软件不会覆盖已有文件；每次执行会在各自文件夹的 _rename_logs 里生成 CSV 日志；"
    "误操作可用“撤销最近一次”或“选择日志撤销”。"
)


class Tooltip:
    """鼠标悬停在控件上停留片刻后，弹出一小块说明气泡，移开即消失。"""

    def __init__(self, widget, text: str, delay: int = 450, wraplength: int = 320) -> None:
        self.widget = widget
        self.text = text
        self.delay = delay
        self.wraplength = wraplength
        self._after_id: str | None = None
        self._tip: tk.Toplevel | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event=None) -> None:
        self._cancel()
        self._after_id = self.widget.after(self.delay, self._show)

    def _cancel(self) -> None:
        if self._after_id is not None:
            try:
                self.widget.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None

    def _show(self) -> None:
        if self._tip is not None:
            return
        try:
            x = self.widget.winfo_rootx() + 12
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        except tk.TclError:
            return
        tip = tk.Toplevel(self.widget)
        tip.wm_overrideredirect(True)
        tip.wm_geometry(f"+{x}+{y}")
        tip.attributes("-topmost", True)
        tk.Label(
            tip,
            text=self.text,
            justify=tk.LEFT,
            background="#ffffe0",
            foreground="#333333",
            relief=tk.SOLID,
            borderwidth=1,
            wraplength=self.wraplength,
            padx=8,
            pady=5,
        ).pack()
        self._tip = tip

    def _hide(self, _event=None) -> None:
        self._cancel()
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None


class VideoRenamerApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("视频批量重命名工具")
        self.minsize(820, 560)

        settings = load_settings()

        mode = str(settings.get("mode", "sequential"))
        if mode not in ("sequential", "episode"):
            mode = "sequential"
        self.mode = tk.StringVar(value=mode)
        self.start_number = tk.StringVar(value=str(settings.get("start_number", "1")))
        self.title_text = tk.StringVar(value=str(settings.get("title", "")))
        self.number_width = tk.StringVar(value=str(settings.get("number_width", "1")))
        self.keep_extension_case = tk.BooleanVar(value=bool(settings.get("keep_extension_case", False)))
        self.cross_folder = tk.BooleanVar(value=bool(settings.get("cross_folder", False)))
        self.only_errors = tk.BooleanVar(value=False)
        self.status_text = tk.StringVar(value="请先添加文件夹并生成预览。")
        self.hint_text = tk.StringVar(value="")
        self.progress_text = tk.StringVar(value="")
        self.folder_summary = tk.StringVar(value="")
        self.example_text = tk.StringVar(value="")  # 实时“改名后示例”

        # 每种模式各自记住上次用的模板，切换模式时不会互相覆盖。
        self._template_by_mode = dict(DEFAULT_TEMPLATE)
        saved_template = str(settings.get("template", "")).strip()
        if saved_template:
            self._template_by_mode[mode] = saved_template
        self._active_template_mode = mode
        self.template = tk.StringVar(value=self._template_by_mode[mode])

        # 统一的文件夹列表：所有操作都基于它，不再有“单选 vs 队列”两套。
        self.folders: list[str] = []
        self._folder_counts: dict[str, int | None] = {}
        legacy_folders = settings.get("folders")
        if isinstance(legacy_folders, list):
            for item in legacy_folders:
                self._append_folder(str(item), count=None)
        legacy_single = str(settings.get("folder", "")).strip()
        if legacy_single:
            self._append_folder(legacy_single, count=None)

        self.current_plans: list[RenamePlan] = []
        self._preview_valid = False
        self.worker_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.display_rows: list[tuple[RenamePlan, object]] = []
        self.sort_column = ""
        self.sort_reverse = False
        self._busy_buttons: list[ttk.Button] = []
        self._busy = False  # 执行/撤销进行中：用于拦截快捷键重复触发

        self._restore_geometry(settings.get("window_geometry"))
        self._setup_theme()
        self._build_ui()
        self._attach_tooltips()
        self._refresh_folder_list()
        self._toggle_mode_inputs()
        self._update_example()
        self._show_empty_hint()
        self._register_setting_traces()
        self._bind_shortcuts()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._poll_worker_queue)

    # ----- 初始化辅助 -----
    def _restore_geometry(self, geometry: object) -> None:
        self.geometry("1280x760")
        if isinstance(geometry, str) and GEOMETRY_RE.match(geometry):
            try:
                self.geometry(geometry)
            except tk.TclError:
                pass

    def _setup_theme(self) -> None:
        """零依赖现代配色：基于内置 clam 主题，自定义卡片、按钮、表格、进度条等样式。"""
        self.configure(bg=COLOR_BG)
        self.option_add("*Font", FONT_BASE)  # 让 Listbox / Menu 等原生控件也用统一字体
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            return

        style.configure(".", background=COLOR_CARD, foreground=COLOR_TEXT, font=FONT_BASE)
        style.configure("TLabel", background=COLOR_CARD, foreground=COLOR_TEXT)
        style.configure("TCheckbutton", background=COLOR_CARD, foreground=COLOR_TEXT)
        style.map("TCheckbutton", background=[("active", COLOR_CARD)])
        style.configure("TRadiobutton", background=COLOR_CARD, foreground=COLOR_TEXT)
        style.map("TRadiobutton", background=[("active", COLOR_CARD)])
        style.configure("TFrame", background=COLOR_CARD)

        # 卡片之间的灰底容器与标签
        style.configure("Bg.TFrame", background=COLOR_BG)
        style.configure("Bg.TLabel", background=COLOR_BG, foreground=COLOR_TEXT)
        style.configure("Muted.TLabel", background=COLOR_CARD, foreground=COLOR_MUTED)
        style.configure("Hint.TLabel", background=COLOR_BG, foreground=COLOR_ERROR)

        # 卡片：白底 + 细边 + 蓝色粗体标题
        style.configure("Card.TLabelframe", background=COLOR_CARD, bordercolor=COLOR_BORDER, relief="solid", borderwidth=1)
        style.configure("Card.TLabelframe.Label", background=COLOR_CARD, foreground=COLOR_ACCENT, font=FONT_TITLE)

        # 普通按钮（扁平浅灰）
        style.configure("TButton", background=COLOR_BTN, foreground=COLOR_TEXT, bordercolor=COLOR_BORDER, relief="flat", padding=(12, 6))
        style.map("TButton", background=[("active", COLOR_BTN_ACTIVE), ("disabled", "#f0f1f4")], foreground=[("disabled", "#a4abb6")])
        # 下拉按钮（撤销 ▾）外观与普通按钮一致
        style.configure("TMenubutton", background=COLOR_BTN, foreground=COLOR_TEXT, bordercolor=COLOR_BORDER, relief="flat", padding=(12, 6), arrowcolor=COLOR_TEXT)
        style.map("TMenubutton", background=[("active", COLOR_BTN_ACTIVE), ("disabled", "#f0f1f4")], foreground=[("disabled", "#a4abb6")])
        # 主按钮（蓝底白字）
        style.configure("Accent.TButton", background=COLOR_ACCENT, foreground="#ffffff", bordercolor=COLOR_ACCENT, relief="flat", padding=(12, 6), font=FONT_HEAD)
        style.map("Accent.TButton", background=[("active", COLOR_ACCENT_DARK), ("disabled", "#b9c6e6")], foreground=[("disabled", "#eef1f6")])

        # 输入框 / 下拉框：白色字段
        for name in ("TEntry", "TCombobox", "TSpinbox"):
            style.configure(name, fieldbackground=COLOR_FIELD, background=COLOR_FIELD, bordercolor=COLOR_BORDER, arrowcolor=COLOR_TEXT, padding=4)
        style.map("TCombobox", fieldbackground=[("readonly", COLOR_FIELD)])

        # 进度条（蓝色）
        style.configure("Horizontal.TProgressbar", background=COLOR_ACCENT, troughcolor="#dfe3ea", bordercolor="#dfe3ea", lightcolor=COLOR_ACCENT, darkcolor=COLOR_ACCENT)

        # 预览表格
        style.configure("Treeview", background=COLOR_FIELD, fieldbackground=COLOR_FIELD, foreground=COLOR_TEXT, rowheight=26, borderwidth=1, bordercolor=COLOR_BORDER)
        style.map("Treeview", background=[("selected", COLOR_SEL)], foreground=[("selected", COLOR_TEXT)])
        style.configure("Treeview.Heading", background=COLOR_BTN, foreground=COLOR_TEXT, font=FONT_HEAD, relief="flat", padding=4)
        style.map("Treeview.Heading", background=[("active", COLOR_BTN_ACTIVE)])

        # 滚动条
        style.configure("TScrollbar", background=COLOR_BTN, troughcolor=COLOR_BG, bordercolor=COLOR_BG, arrowcolor=COLOR_TEXT)

        # 灰底上的勾选框（如“只看出错的”）
        style.configure("Bg.TCheckbutton", background=COLOR_BG, foreground=COLOR_TEXT)
        style.map("Bg.TCheckbutton", background=[("active", COLOR_BG)])
        # 操作引导小字（灰底）
        style.configure("Guide.TLabel", background=COLOR_BG, foreground="#3a4151")
        # 实时示例（白卡片上、蓝色粗体）
        style.configure("Example.TLabel", background=COLOR_CARD, foreground=COLOR_ACCENT, font=FONT_HEAD)
        # 可点击的“高级设置”折叠条
        style.configure("Toggle.TLabel", background=COLOR_CARD, foreground=COLOR_ACCENT)

    def _append_folder(self, folder: str, count: int | None) -> bool:
        if not folder or not folder.strip():
            return False
        folder = str(Path(folder))
        if folder in self.folders:
            return False
        self.folders.append(folder)
        self._folder_counts[folder] = count
        return True

    def _build_ui(self) -> None:
        # 整窗纵向滚动：窗口放不下时可滚动看全（小屏/缩放也不截断）；放得下时内容撑满。
        container = ttk.Frame(self, style="Bg.TFrame")
        container.pack(fill=tk.BOTH, expand=True)
        self._canvas = tk.Canvas(container, bg=COLOR_BG, highlightthickness=0)
        vscroll = ttk.Scrollbar(container, orient=tk.VERTICAL, command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=vscroll.set)
        vscroll.pack(side=tk.RIGHT, fill=tk.Y)
        self._canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._outer = ttk.Frame(self._canvas, padding=12, style="Bg.TFrame")
        outer = self._outer
        self._outer_window = self._canvas.create_window((0, 0), window=outer, anchor="nw")
        outer.bind("<Configure>", lambda e: self._canvas.configure(scrollregion=self._canvas.bbox("all")))
        self._canvas.bind("<Configure>", self._on_canvas_configure)
        self.bind_all("<MouseWheel>", self._on_mousewheel)

        folder_frame = ttk.LabelFrame(outer, text="①  选择要处理的文件夹（可添加多个）", padding=10, style="Card.TLabelframe")
        folder_frame.pack(fill=tk.X)

        button_row = ttk.Frame(folder_frame)
        button_row.grid(row=0, column=0, sticky="w")
        self._add_action_button(button_row, "添加文件夹", self._add_folder, style="Accent.TButton",
                                tip="选一个要批量改名的视频文件夹加进列表，可以加多个。")
        self._add_action_button(button_row, "移除选中", self._remove_selected_folders,
                                tip="把列表里选中的文件夹移出去（不会动你的真实文件）。")
        self._add_action_button(button_row, "清空列表", self._clear_folders, tip="清空整个文件夹列表，重新选。")
        ttk.Label(folder_frame, textvariable=self.folder_summary, style="Muted.TLabel").grid(
            row=0, column=1, sticky="e"
        )
        folder_frame.columnconfigure(0, weight=1)

        list_wrap = ttk.Frame(folder_frame)
        list_wrap.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        list_wrap.columnconfigure(0, weight=1)
        self.folder_listbox = tk.Listbox(
            list_wrap,
            height=4,
            selectmode=tk.EXTENDED,
            activestyle="none",
            bg=COLOR_FIELD,
            fg=COLOR_TEXT,
            selectbackground=COLOR_SEL,
            selectforeground=COLOR_TEXT,
            relief="solid",
            borderwidth=1,
            highlightthickness=1,
            highlightbackground=COLOR_BORDER,
            highlightcolor=COLOR_ACCENT,
        )
        self.folder_listbox.grid(row=0, column=0, sticky="ew")
        folder_scroll = ttk.Scrollbar(list_wrap, orient=tk.VERTICAL, command=self.folder_listbox.yview)
        self.folder_listbox.configure(yscrollcommand=folder_scroll.set)
        folder_scroll.grid(row=0, column=1, sticky="ns")

        self.folder_menu = tk.Menu(self, tearoff=0)
        self.folder_menu.add_command(label="添加文件夹", command=self._add_folder)
        self.folder_menu.add_command(label="在资源管理器中打开", command=self._open_selected_folder)
        self.folder_menu.add_separator()
        self.folder_menu.add_command(label="移除选中", command=self._remove_selected_folders)
        self.folder_menu.add_command(label="清空列表", command=self._clear_folders)

        mode_frame = ttk.LabelFrame(outer, text="②  怎么给视频命名", padding=10, style="Card.TLabelframe")
        mode_frame.pack(fill=tk.X, pady=(10, 0))

        # 模式一：连续编号
        self.seq_radio = ttk.Radiobutton(
            mode_frame, text="连续编号", variable=self.mode, value="sequential", command=self._on_mode_changed
        )
        self.seq_radio.grid(row=0, column=0, sticky="w")
        ttk.Label(mode_frame, text="文件名乱、不管原名，从头按顺序排号", style="Muted.TLabel").grid(
            row=0, column=1, sticky="w", padx=(8, 0)
        )
        seq_row = ttk.Frame(mode_frame)
        seq_row.grid(row=1, column=0, columnspan=2, sticky="w", padx=(24, 0), pady=(2, 8))
        ttk.Label(seq_row, text="从第几号开始：").pack(side=tk.LEFT)
        self.start_entry = ttk.Entry(seq_row, textvariable=self.start_number, width=8)
        self.start_entry.pack(side=tk.LEFT, padx=(0, 16))
        self.cross_folder_check = ttk.Checkbutton(seq_row, text="跨文件夹连续编号", variable=self.cross_folder)
        self.cross_folder_check.pack(side=tk.LEFT)

        # 模式二：识别集数
        self.ep_radio = ttk.Radiobutton(
            mode_frame, text="识别集数", variable=self.mode, value="episode", command=self._on_mode_changed
        )
        self.ep_radio.grid(row=2, column=0, sticky="w")
        ttk.Label(mode_frame, text="文件名里已有集数（第1集 / 01 / EP6 / 超清-6），按集数来命名", style="Muted.TLabel").grid(
            row=2, column=1, sticky="w", padx=(8, 0)
        )
        ep_row = ttk.Frame(mode_frame)
        ep_row.grid(row=3, column=0, columnspan=2, sticky="w", padx=(24, 0), pady=(2, 8))
        ttk.Label(ep_row, text="剧名：").pack(side=tk.LEFT)
        self.title_entry = ttk.Entry(ep_row, textvariable=self.title_text, width=24)
        self.title_entry.pack(side=tk.LEFT)

        # 实时“改名后示例”
        example_row = ttk.Frame(mode_frame)
        example_row.grid(row=4, column=0, columnspan=2, sticky="w")
        ttk.Label(example_row, text="改名后示例：").pack(side=tk.LEFT)
        ttk.Label(example_row, textvariable=self.example_text, style="Example.TLabel").pack(side=tk.LEFT)

        # 高级设置（默认折叠）
        self.advanced_toggle = ttk.Label(
            mode_frame, text="▸ 高级设置（命名格式、序号位数、扩展名）", style="Toggle.TLabel", cursor="hand2"
        )
        self.advanced_toggle.grid(row=5, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self.advanced_toggle.bind("<Button-1>", lambda e: self._toggle_advanced())

        self.advanced_frame = ttk.Frame(mode_frame)
        self.advanced_frame.grid(row=6, column=0, columnspan=2, sticky="w", padx=(24, 0), pady=(4, 0))
        ttk.Label(self.advanced_frame, text="命名格式：").grid(row=0, column=0, sticky="e")
        self.template_combo = ttk.Combobox(self.advanced_frame, textvariable=self.template, values=SEQUENTIAL_TEMPLATES, width=24)
        self.template_combo.grid(row=0, column=1, sticky="w", padx=(0, 16))
        ttk.Label(self.advanced_frame, text="序号位数：").grid(row=0, column=2, sticky="e")
        self.width_combo = ttk.Combobox(
            self.advanced_frame, textvariable=self.number_width, values=["1", "2", "3"], width=5, state="readonly"
        )
        self.width_combo.grid(row=0, column=3, sticky="w", padx=(0, 16))
        self.keep_ext_check = ttk.Checkbutton(self.advanced_frame, text="保持扩展名大小写", variable=self.keep_extension_case)
        self.keep_ext_check.grid(row=0, column=4, sticky="w")
        self.advanced_frame.grid_remove()  # 默认收起
        self._advanced_visible = False

        self.guide_label = ttk.Label(
            outer,
            text="操作两步走：先点『生成预览』看效果 → 确认没有红色错误 → 再点『确认执行改名』",
            style="Guide.TLabel",
            wraplength=1180,
            justify=tk.LEFT,
        )
        self.guide_label.pack(fill=tk.X, pady=(10, 0))

        # 第一行：主操作
        action_frame = ttk.Frame(outer, style="Bg.TFrame")
        action_frame.pack(fill=tk.X, pady=(4, 0))
        self._add_action_button(
            action_frame, "生成预览", self._preview, style="Accent.TButton",
            tip="扫描文件夹，先看看每个文件会改成什么名字（不会真的改）。",
        )
        self.execute_button = ttk.Button(action_frame, text="确认执行改名", command=self._execute, state=tk.DISABLED, style="Accent.TButton")
        self.execute_button.pack(side=tk.LEFT, padx=(8, 0))
        self._busy_buttons.append(self.execute_button)
        Tooltip(self.execute_button, "按预览的结果真正改名。预览没问题（没有红色）时才能点。")
        self._add_action_button(action_frame, "清空预览", self._clear_preview, padx=(8, 0), tip="清掉下面的预览清单，重新来。")

        # 第二行：辅助操作 + 状态（拆成两行，窗口变窄时不会被截断）
        action_frame2 = ttk.Frame(outer, style="Bg.TFrame")
        action_frame2.pack(fill=tk.X, pady=(6, 0))
        self.undo_menubutton = ttk.Menubutton(action_frame2, text="撤销")
        undo_menu = tk.Menu(self.undo_menubutton, tearoff=0)
        undo_menu.add_command(label="撤销最近一次", command=self._undo_latest)
        undo_menu.add_command(label="选择日志撤销…", command=self._undo_from_file)
        self.undo_menubutton.configure(menu=undo_menu)
        self.undo_menubutton.pack(side=tk.LEFT)
        self._busy_buttons.append(self.undo_menubutton)
        Tooltip(self.undo_menubutton, "改错了别慌：撤销最近一次，或挑某条日志撤销，恢复到改名前。")

        self.only_errors_check = ttk.Checkbutton(
            action_frame2, text="只看出错的", variable=self.only_errors, command=self._render_plans, style="Bg.TCheckbutton"
        )
        self.only_errors_check.pack(side=tk.LEFT, padx=(18, 0))
        self._busy_buttons.append(self.only_errors_check)
        Tooltip(self.only_errors_check, "勾选后只显示有问题（红色）的项，方便排查。")

        ttk.Label(action_frame2, textvariable=self.status_text, style="Bg.TLabel").pack(side=tk.RIGHT)

        self.hint_label = ttk.Label(outer, textvariable=self.hint_text, style="Hint.TLabel", wraplength=1180, justify=tk.LEFT)
        self.hint_label.pack(fill=tk.X, pady=(6, 0))

        progress_frame = ttk.Frame(outer, style="Bg.TFrame")
        progress_frame.pack(fill=tk.X, pady=(8, 0))
        self.progress = ttk.Progressbar(progress_frame, mode="determinate", maximum=100)
        self.progress.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
        ttk.Label(progress_frame, textvariable=self.progress_text, width=38, style="Bg.TLabel").pack(side=tk.RIGHT)

        preview_frame = ttk.LabelFrame(outer, text="③  预览清单（确认无误后再执行）", padding=10, style="Card.TLabelframe")
        preview_frame.pack(fill=tk.BOTH, expand=True, pady=(10, 0))

        columns = ("folder", "old_name", "new_name", "episode", "status")
        self.tree = ttk.Treeview(preview_frame, columns=columns, show="headings")
        self._base_headings = {
            "folder": "文件夹",
            "old_name": "原文件名",
            "new_name": "新文件名",
            "episode": "集",
            "status": "状态",
        }
        widths = {"folder": 130, "old_name": 185, "new_name": 195, "episode": 45, "status": 135}
        for column in columns:
            self.tree.heading(column, text=self._base_headings[column], command=lambda c=column: self._sort_by(c))
            self.tree.column(
                column, width=widths[column], minwidth=40, stretch=True,
                anchor="center" if column == "episode" else "w",
            )
        self.tree.tag_configure("error", foreground=COLOR_ERROR)
        self.tree.tag_configure("ok", foreground=COLOR_OK)
        self.tree.tag_configure("hint", foreground=COLOR_MUTED)
        self.tree.bind("<Double-1>", self._on_preview_double_click)

        y_scroll = ttk.Scrollbar(preview_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=y_scroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        ttk.Label(
            preview_frame,
            text="提示：点表头可排序；双击某一行可在资源管理器中定位该文件（完整路径在那里看）。",
            style="Muted.TLabel",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))
        preview_frame.rowconfigure(0, weight=1)
        preview_frame.columnconfigure(0, weight=1)

    def _add_action_button(self, parent, text, command, padx=(0, 6), style=None, tip=None) -> ttk.Button:
        button = ttk.Button(parent, text=text, command=command)
        if style:
            button.configure(style=style)
        button.pack(side=tk.LEFT, padx=padx)
        self._busy_buttons.append(button)
        if tip:
            Tooltip(button, tip)
        return button

    def _attach_tooltips(self) -> None:
        tips = {
            self.seq_radio: "按顺序重新编号，忽略原文件名。适合文件名杂乱、只想从头排号的情况。",
            self.ep_radio: "从文件名里识别集数（第1集 / 01 / 第十一集 / EP6 / 超清-6 等）后改名。适合本来就带集数的情况。",
            self.start_entry: "改名后的第一个数字。例如填 1 → 1.mp4、2.mp4……",
            self.cross_folder_check: (
                "勾选后多个文件夹接续编号（A→1,2,3，B→4,5）；不勾选则每个文件夹都从起始数字重新开始。"
                "注意：文件仍各自留在原文件夹，不会合并到一起。"
            ),
            self.title_entry: "配合模板里的 {title} 使用。例如剧名「庆余年」+ 模板 {title}-第{episode}集 → 庆余年-第1集.mp4。",
            self.template_combo: "命名格式：{number} 连续编号；{episode} 识别到的集数；{title} 剧名。可下拉选，也可手动输入。",
            self.width_combo: "序号位数（补零）：1→1，2→01，3→001。位数补齐能让文件名按数字大小正确排序。",
            self.keep_ext_check: "默认把扩展名统一成小写（.MP4 → .mp4）；勾选则保留原样。",
            self.advanced_toggle: "点开可调命名格式、序号位数、扩展名等进阶选项；不懂可以先不管。",
            self.folder_listbox: "双击可在资源管理器打开该文件夹；右键有更多操作；选中后按 Delete 移除。",
        }
        for widget, text in tips.items():
            Tooltip(widget, text)

    def _register_setting_traces(self) -> None:
        for var in (
            self.mode,
            self.start_number,
            self.title_text,
            self.number_width,
            self.template,
            self.keep_extension_case,
            self.cross_folder,
        ):
            var.trace_add("write", self._on_setting_changed)

    def _bind_shortcuts(self) -> None:
        self.bind("<Control-o>", lambda e: self._add_folder())
        self.bind("<Control-O>", lambda e: self._add_folder())
        self.bind("<Control-p>", lambda e: self._preview())
        self.bind("<Control-P>", lambda e: self._preview())
        self.bind("<Control-Return>", lambda e: self._execute())
        self.bind("<Control-z>", lambda e: self._undo_latest())
        self.bind("<Control-Z>", lambda e: self._undo_latest())
        self.bind("<F1>", lambda e: self._show_help_dialog())  # 帮助按钮已移除，保留 F1 兜底
        self.folder_listbox.bind("<Delete>", lambda e: self._remove_selected_folders())
        self.folder_listbox.bind("<Double-Button-1>", lambda e: self._open_selected_folder())
        self.folder_listbox.bind("<Button-3>", self._show_folder_menu)
        self.bind("<Configure>", self._on_resize, add="+")

    def _on_resize(self, event) -> None:
        # 窗口尺寸变化时，让长文字（引导/提示）按当前宽度换行，避免变窄被截断。
        if event.widget is not self:
            return
        width = max(240, event.width - 64)
        self.guide_label.configure(wraplength=width)
        self.hint_label.configure(wraplength=width)

    def _on_canvas_configure(self, event) -> None:
        # 内容宽度跟随画布；内容不足一屏时撑满画布高度，让预览表仍能纵向铺满。
        self._canvas.itemconfigure(self._outer_window, width=event.width)
        self._canvas.itemconfigure(self._outer_window, height=max(event.height, self._outer.winfo_reqheight()))

    def _on_mousewheel(self, event) -> None:
        step = -1 if event.delta > 0 else 1
        widget = event.widget
        try:
            # 指针在预览表上就滚动表本身，否则滚动整页。
            if widget is self.tree or str(widget).startswith(str(self.tree)):
                self.tree.yview_scroll(step, "units")
            else:
                self._canvas.yview_scroll(step, "units")
        except tk.TclError:
            pass

    # ----- 文件夹列表 -----
    def _count_videos(self, folder: str) -> int | None:
        try:
            return len(find_videos(folder))
        except Exception:
            return None

    def _add_folder(self) -> None:
        if self._busy:
            return
        folder = filedialog.askdirectory(title="选择要处理的视频文件夹", parent=self)
        if not folder:
            return
        folder = str(Path(folder))
        if folder in self.folders:
            messagebox.showinfo("已在列表中", "该文件夹已经在列表里了。", parent=self)
            return
        self._append_folder(folder, count=None)
        self._refresh_folder_list()
        self._invalidate_preview()
        self._start_folder_count(folder)

    def _start_folder_count(self, folder: str) -> None:
        # 后台统计视频数量，避免超大目录递归扫描时卡住界面。
        def work() -> None:
            count = self._count_videos(folder)
            self.worker_queue.put(("count", (folder, count)))

        threading.Thread(target=work, daemon=True).start()

    def _apply_folder_count(self, payload: object) -> None:
        folder, count = payload  # type: ignore[misc]
        if folder not in self.folders:
            return  # 该文件夹在统计返回前已被移除
        self._folder_counts[folder] = count
        self._refresh_folder_list()

    def _remove_selected_folders(self) -> None:
        if self._busy:
            return
        selection = list(self.folder_listbox.curselection())
        if not selection:
            return
        for index in reversed(selection):
            folder = self.folders[index]
            del self.folders[index]
            self._folder_counts.pop(folder, None)
        self._refresh_folder_list()
        self._invalidate_preview()

    def _clear_folders(self) -> None:
        if self._busy:
            return
        if not self.folders:
            return
        self.folders.clear()
        self._folder_counts.clear()
        self._refresh_folder_list()
        self._invalidate_preview()

    def _open_selected_folder(self) -> None:
        selection = self.folder_listbox.curselection()
        if not selection:
            return
        folder = self.folders[selection[0]]
        if hasattr(os, "startfile"):
            try:
                os.startfile(folder)  # type: ignore[attr-defined]
            except OSError as exc:
                messagebox.showerror("无法打开", f"打不开这个文件夹：\n{folder}\n{exc}", parent=self)

    def _show_folder_menu(self, event) -> None:
        index = self.folder_listbox.nearest(event.y)
        if index >= 0 and index < len(self.folders) and not self.folder_listbox.selection_includes(index):
            self.folder_listbox.selection_clear(0, tk.END)
            self.folder_listbox.selection_set(index)
        try:
            self.folder_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.folder_menu.grab_release()

    def _refresh_folder_list(self) -> None:
        self.folder_listbox.delete(0, tk.END)
        for folder in self.folders:
            count = self._folder_counts.get(folder)
            label = folder if not isinstance(count, int) else f"{folder}    （{count} 个视频）"
            self.folder_listbox.insert(tk.END, label)
        self._update_folder_summary()

    def _update_folder_summary(self) -> None:
        count = len(self.folders)
        if count == 0:
            self.folder_summary.set("还没有添加文件夹")
            return
        known = [self._folder_counts[f] for f in self.folders if isinstance(self._folder_counts.get(f), int)]
        if len(known) == count:
            self.folder_summary.set(f"共 {count} 个文件夹，{sum(known)} 个视频")
        else:
            self.folder_summary.set(f"共 {count} 个文件夹")

    # ----- 命名设置 -----
    def _show_help_dialog(self) -> None:
        messagebox.showinfo("使用说明", HELP_TEXT, parent=self)

    def _on_mode_changed(self) -> None:
        new_mode = self.mode.get()
        if new_mode != self._active_template_mode:
            self._template_by_mode[self._active_template_mode] = self.template.get()
            self.template.set(self._template_by_mode.get(new_mode, DEFAULT_TEMPLATE[new_mode]))
            self._active_template_mode = new_mode
        self._toggle_mode_inputs()

    def _toggle_mode_inputs(self) -> None:
        is_sequential = self.mode.get() == "sequential"
        self.start_entry.configure(state=tk.NORMAL if is_sequential else tk.DISABLED)
        self.cross_folder_check.configure(state=tk.NORMAL if is_sequential else tk.DISABLED)
        self.title_entry.configure(state=tk.DISABLED if is_sequential else tk.NORMAL)
        self.template_combo.configure(values=SEQUENTIAL_TEMPLATES if is_sequential else EPISODE_TEMPLATES)

    def _on_setting_changed(self, *_args) -> None:
        self._invalidate_preview()
        self._update_example()

    def _invalidate_preview(self) -> None:
        if self._busy:
            return
        self._preview_valid = False
        self.execute_button.configure(state=tk.DISABLED)
        if self.current_plans:
            self.status_text.set("设置已改变，请重新点“生成预览”。")
            self.progress["value"] = 0

    def _toggle_advanced(self) -> None:
        self._advanced_visible = not self._advanced_visible
        if self._advanced_visible:
            self.advanced_frame.grid()
            self.advanced_toggle.configure(text="▾ 高级设置（命名格式、序号位数、扩展名）")
        else:
            self.advanced_frame.grid_remove()
            self.advanced_toggle.configure(text="▸ 高级设置（命名格式、序号位数、扩展名）")

    def _read_options_safe(self) -> RenameOptions:
        """读取当前设置但不弹错误框（解析失败回退默认值），用于实时示例。"""
        try:
            width = int(self.number_width.get())
        except ValueError:
            width = 1
        if width not in (1, 2, 3):
            width = 1
        if self.mode.get() == "sequential":
            try:
                start = int(self.start_number.get().strip())
            except ValueError:
                start = 1
            return RenameOptions(
                mode="sequential",
                start_number=max(0, start),
                number_width=width,
                template=self.template.get(),
                keep_extension_case=self.keep_extension_case.get(),
            )
        template = self.template.get()
        return RenameOptions(
            mode="episode",
            episode_output="title" if "{title}" in template else "episode_only",
            title=self.title_text.get() or "剧名",
            number_width=width,
            template=template,
            keep_extension_case=self.keep_extension_case.get(),
        )

    def _update_example(self) -> None:
        try:
            names = example_names(self._read_options_safe(), count=3)
        except Exception:
            names = []
        self.example_text.set("、".join(names) if names else "（当前设置无法生成，请检查命名格式）")

    def _read_options(self) -> RenameOptions | None:
        mode = self.mode.get()
        try:
            width = int(self.number_width.get())
        except ValueError:
            messagebox.showerror("输入错误", "补零位数必须是 1、2 或 3。", parent=self)
            return None

        if mode == "sequential":
            try:
                start = int(self.start_number.get().strip())
            except ValueError:
                messagebox.showerror("输入错误", "起始数字必须是整数。", parent=self)
                return None
            if start < 0:
                messagebox.showerror("输入错误", "起始数字不能小于 0。", parent=self)
                return None
            return RenameOptions(
                mode="sequential",
                start_number=start,
                number_width=width,
                template=self.template.get(),
                keep_extension_case=self.keep_extension_case.get(),
            )

        template = self.template.get()
        return RenameOptions(
            mode="episode",
            episode_output="title" if "{title}" in template else "episode_only",
            title=self.title_text.get(),
            number_width=width,
            template=template,
            keep_extension_case=self.keep_extension_case.get(),
        )

    def _folders_for_action(self) -> list[str]:
        return list(self.folders)

    # ----- 预览 -----
    def _preview(self) -> None:
        if self._busy:
            return
        folders = self._folders_for_action()
        if not folders:
            messagebox.showerror("没有文件夹", "请先点“添加文件夹”，至少添加一个文件夹。", parent=self)
            return
        options = self._read_options()
        if options is None:
            return

        continuous = self.cross_folder.get() and options.mode == "sequential"
        try:
            plans = build_rename_plans([Path(folder) for folder in folders], options, continuous_numbering=continuous)
        except Exception as exc:
            messagebox.showerror("预览失败", f"生成预览时发生错误：\n{exc}", parent=self)
            return

        self.current_plans = plans
        for folder, plan in zip(folders, plans):
            self._folder_counts[folder] = len(plan.items)
        self._refresh_folder_list()
        self._preview_valid = True
        self._save_current_settings()
        self._render_plans()

    def _render_plans(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self.display_rows = []
        if not self.current_plans:
            self._show_empty_hint()
            return

        rows: list[tuple[RenamePlan, object]] = []
        for plan in self.current_plans:
            for item in plan.items:
                if self.only_errors.get() and item.ok:
                    continue
                rows.append((plan, item))

        if self.sort_column:
            rows.sort(key=lambda row: self._row_value(row[0], row[1], self.sort_column), reverse=self.sort_reverse)
        self.display_rows = rows

        for plan, item in rows:
            tag = "ok" if item.ok else "error"
            episode_text = "" if item.episode_number is None else str(item.episode_number)
            self.tree.insert(
                "",
                tk.END,
                values=(
                    plan.root.name,
                    item.old_path.name,
                    item.new_path.name,
                    episode_text,
                    item.status,
                ),
                tags=(tag,),
            )
        self._update_sort_indicators()

        total_items = sum(len(plan.items) for plan in self.current_plans)
        can_execute = self._preview_valid and bool(self.current_plans) and all(plan.can_execute for plan in self.current_plans)
        if can_execute:
            self.status_text.set(f"预览完成：{len(self.current_plans)} 个文件夹，{total_items} 个视频可执行。")
        else:
            self.status_text.set("预览存在冲突或错误，请检查红色项目。")
        self.hint_text.set(self._build_hint())
        self.execute_button.configure(state=tk.NORMAL if (can_execute and not self._busy) else tk.DISABLED)

    def _build_hint(self) -> str:
        messages: list[str] = []
        for plan in self.current_plans:
            for error in plan.errors:
                if error not in messages:
                    messages.append(error)
        seen_types: list[str] = []
        for plan in self.current_plans:
            for item in plan.items:
                if not item.ok and item.error_type and item.error_type not in seen_types:
                    seen_types.append(item.error_type)
        for error_type in seen_types:
            tip = STATUS_TIPS.get(error_type)
            if tip and tip not in messages:
                messages.append(tip)
        return "  ".join(messages)

    def _show_empty_hint(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self.tree.insert(
            "",
            tk.END,
            values=(
                "操作指引",
                "① 添加文件夹",
                "② 命名设置 → ③ 生成预览",
                "",
                "④ 确认执行改名（按 F1 看说明）",
            ),
            tags=("hint",),
        )

    def _row_value(self, plan: RenamePlan, item: object, column: str) -> object:
        if column == "folder":
            return plan.root.name.casefold()
        if column == "old_name":
            return item.old_path.name.casefold()
        if column == "new_name":
            return item.new_path.name.casefold()
        if column == "old":
            return str(item.old_path).casefold()
        if column == "new":
            return str(item.new_path).casefold()
        if column == "episode":
            return item.episode_number if item.episode_number is not None else 10**9
        if column == "status":
            return item.status.casefold()
        return ""

    def _sort_by(self, column: str) -> None:
        if self.sort_column == column:
            self.sort_reverse = not self.sort_reverse
        else:
            self.sort_column = column
            self.sort_reverse = False
        self._render_plans()

    def _update_sort_indicators(self) -> None:
        for column, base in self._base_headings.items():
            text = base
            if column == self.sort_column:
                text = f"{base} {'▼' if self.sort_reverse else '▲'}"
            self.tree.heading(column, text=text)

    def _on_preview_double_click(self, event) -> None:
        pair = self._plan_item_for_tree_id(self.tree.identify_row(event.y))
        if pair is None:
            return
        _plan, item = pair
        self._reveal_path(item.old_path if item.old_path.exists() else item.new_path)

    def _plan_item_for_tree_id(self, item_id: str):
        """把预览表里的一行映射回 (plan, item)；空行或操作指引行返回 None。"""
        if not item_id:
            return None
        try:
            index = self.tree.index(item_id)
        except tk.TclError:
            return None
        if 0 <= index < len(self.display_rows):
            return self.display_rows[index]
        return None

    def _reveal_path(self, path) -> None:
        """在资源管理器中定位文件；文件不在了就退而打开其所在目录。"""
        target = Path(path)
        try:
            if os.name == "nt" and target.exists():
                subprocess.Popen(["explorer", "/select,", str(target)])
            elif hasattr(os, "startfile") and target.parent.exists():
                os.startfile(str(target.parent))  # type: ignore[attr-defined]
            elif target.parent.exists():
                subprocess.Popen(["xdg-open", str(target.parent)])
        except OSError:
            pass

    # ----- 执行 -----
    def _execute(self) -> None:
        if self._busy:
            return
        if not self._preview_valid or not self.current_plans or not all(plan.can_execute for plan in self.current_plans):
            messagebox.showerror("不能执行", "当前没有有效预览，请先点“生成预览”。", parent=self)
            return

        total = sum(len(plan.items) for plan in self.current_plans)
        confirmed = messagebox.askyesno(
            "确认执行",
            f"即将处理 {len(self.current_plans)} 个文件夹，共 {total} 个视频。\n\n每个文件夹会单独生成日志。\n确定继续吗？",
            parent=self,
        )
        if not confirmed:
            return

        self._set_busy(True)
        self.progress["value"] = 0
        self.status_text.set("正在执行，请稍候...")
        plans = list(self.current_plans)
        threading.Thread(target=self._execute_worker, args=(plans,), daemon=True).start()

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        for button in self._busy_buttons:
            button.configure(state=tk.DISABLED if busy else tk.NORMAL)
        self.folder_listbox.configure(state=tk.DISABLED if busy else tk.NORMAL)
        if not busy:
            self._refresh_execute_state()

    def _refresh_execute_state(self) -> None:
        can_execute = self._preview_valid and bool(self.current_plans) and all(
            plan.can_execute for plan in self.current_plans
        )
        self.execute_button.configure(state=tk.NORMAL if can_execute else tk.DISABLED)

    def _execute_worker(self, plans: list[RenamePlan]) -> None:
        try:
            results = []
            grand_total = sum(max(1, len(plan.items) * 2) for plan in plans)
            base = 0
            for plan in plans:
                local_total = max(1, len(plan.items) * 2)

                def progress(done: int, _total: int, message: str, base_step: int = base) -> None:
                    self.worker_queue.put(("progress", (base_step + done, grand_total, message)))

                result = execute_rename_plan(plan, progress_callback=progress)
                results.append(result)
                base += local_total
            self.worker_queue.put(("done", results))
        except Exception:
            self.worker_queue.put(("error", traceback.format_exc()))

    # ----- 撤销 -----
    def _undo_latest(self) -> None:
        if self._busy:
            return
        folders = self._folders_for_action()
        if not folders:
            messagebox.showerror("缺少文件夹", "请先添加要撤销的文件夹。", parent=self)
            return
        latest_log: Path | None = None
        latest_mtime = -1.0
        for folder in folders:
            log_path = find_latest_log(folder)
            if log_path is None:
                continue
            try:
                mtime = log_path.stat().st_mtime
            except OSError:
                continue
            if mtime > latest_mtime:
                latest_mtime = mtime
                latest_log = log_path
        if latest_log is None:
            messagebox.showerror("没有日志", "列表里的文件夹都没有可撤销的日志。", parent=self)
            return
        self._run_undo(latest_log)

    def _undo_from_file(self) -> None:
        if self._busy:
            return
        log_path = filedialog.askopenfilename(
            title="选择重命名日志",
            filetypes=[("CSV 日志", "*.csv"), ("所有文件", "*.*")],
            parent=self,
        )
        if log_path:
            self._run_undo(Path(log_path))

    def _run_undo(self, log_path: str | Path) -> None:
        plan = build_undo_plan(log_path)
        if not plan.can_execute:
            details = "\n".join([item.status for item in plan.items if not item.ok] + plan.errors)
            messagebox.showerror("不能撤销", details or "撤销预览存在错误。", parent=self)
            return
        confirmed = messagebox.askyesno(
            "确认撤销",
            f"即将按日志撤销 {len(plan.items)} 个文件，恢复到改名前。\n确定继续吗？",
            parent=self,
        )
        if not confirmed:
            return
        result = execute_undo_plan(plan)
        if result.success:
            messagebox.showinfo("撤销完成", f"已恢复 {len(result.items)} 个文件。", parent=self)
        else:
            messagebox.showerror("撤销失败", "\n".join(result.errors), parent=self)

    # ----- 后台进度 -----
    def _poll_worker_queue(self) -> None:
        # 一次性排空队列：执行时每个文件会产生多条进度消息，若每 100ms 只处理一条，
        # 大批量时进度条会严重滞后、“完成”提示也会延迟很久。这里每个 tick 把队列里
        # 的进度消息都取出，只用最后一条刷新进度条，done/error 立即处理。
        latest_progress: tuple[int, int, str] | None = None
        try:
            while True:
                kind, payload = self.worker_queue.get_nowait()
                if kind == "progress":
                    latest_progress = payload  # type: ignore[assignment]
                    continue
                if latest_progress is not None:
                    self._apply_progress(latest_progress)
                    latest_progress = None
                if kind == "done":
                    self._handle_execute_done(payload)
                elif kind == "count":
                    self._apply_folder_count(payload)
                else:
                    self._handle_execute_error(payload)
        except queue.Empty:
            pass
        if latest_progress is not None:
            self._apply_progress(latest_progress)
        self.after(50, self._poll_worker_queue)

    def _apply_progress(self, payload: tuple[int, int, str]) -> None:
        done, total, message = payload
        self.progress["value"] = 100 if total <= 0 else int(done / total * 100)
        self.progress_text.set(message)

    def _handle_execute_done(self, payload: object) -> None:
        results = payload
        self._set_busy(False)
        success_count = sum(1 for result in results if result.success)
        failed = [result for result in results if not result.success]
        logs = [str(result.log_path) for result in results if result.log_path]
        if failed:
            errors = "\n".join(error for result in failed for error in result.errors)
            messagebox.showerror(
                "执行完成但有失败",
                f"成功 {success_count} 个文件夹，失败 {len(failed)} 个。\n\n{errors}",
                parent=self,
            )
            self.status_text.set("执行完成但有失败，请查看日志。")
        else:
            renamed = sum(len(result.renamed) for result in results)
            deleted = sum(len(result.deleted_folders) for result in results)
            messagebox.showinfo(
                "执行完成",
                f"成功重命名 {renamed} 个视频。\n删除空文件夹 {deleted} 个。\n日志数：{len(logs)}",
                parent=self,
            )
            self.status_text.set("执行完成。")
            self._clear_preview()
        self.progress["value"] = 100
        self.progress_text.set("完成")

    def _handle_execute_error(self, payload: object) -> None:
        self._set_busy(False)
        messagebox.showerror("执行失败", str(payload), parent=self)
        self.status_text.set("执行失败，请查看错误信息。")

    def _clear_preview(self) -> None:
        self.current_plans = []
        self.display_rows = []
        self._preview_valid = False
        self.sort_column = ""
        self.sort_reverse = False
        self._show_empty_hint()
        self.execute_button.configure(state=tk.DISABLED)
        self.status_text.set("请先添加文件夹并生成预览。")
        self.hint_text.set("")
        self.progress["value"] = 0
        self.progress_text.set("")

    # ----- 配置持久化 -----
    def _save_current_settings(self) -> None:
        self._template_by_mode[self.mode.get()] = self.template.get()
        save_settings(
            {
                "folders": list(self.folders),
                "mode": self.mode.get(),
                "start_number": self.start_number.get(),
                "title": self.title_text.get(),
                "number_width": self.number_width.get(),
                "template": self.template.get(),
                "keep_extension_case": self.keep_extension_case.get(),
                "cross_folder": self.cross_folder.get(),
                "window_geometry": self.geometry(),
            }
        )

    def _on_close(self) -> None:
        self._save_current_settings()
        self.destroy()


def main() -> int:
    app = VideoRenamerApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
