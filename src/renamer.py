from __future__ import annotations

import ctypes
import os
import re
import string
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable

from episode_parser import parse_episode_number
from operation_log import (
    READY_STATUS,
    file_metadata,
    make_log_path,
    move_no_overwrite,
    write_operation_log,
)
from scanner import delete_empty_source_folders, find_videos, natural_sort_key


FILE_ATTRIBUTE_HIDDEN = 0x2
INVALID_FILENAME_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')

# Windows 保留设备名（不区分大小写、忽略扩展名），用这些名字会导致创建文件失败。
WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def _is_reserved_filename(name: str) -> bool:
    """目标文件名（去掉扩展名后）是否撞上 Windows 保留设备名。"""
    stem = Path(name).stem.strip().upper()
    return stem in WINDOWS_RESERVED_NAMES


def make_staging_path(original_path: Path, token: str, index: int) -> Path:
    """Create a temporary rename path that does not start with a dot."""
    safe_name = f"__renaming__{token}__{index}__{original_path.name}.tmp"
    return original_path.with_name(safe_name)


def get_windows_file_attributes(path: str | Path) -> int | None:
    if os.name != "nt":
        return None
    attrs = ctypes.windll.kernel32.GetFileAttributesW(str(Path(path)))
    if attrs == 0xFFFFFFFF:
        return None
    return int(attrs)


def set_windows_file_attributes(path: str | Path, attributes: int) -> bool:
    if os.name != "nt":
        return False
    return bool(ctypes.windll.kernel32.SetFileAttributesW(str(Path(path)), int(attributes)))


def clear_windows_hidden_attribute(path: str | Path) -> None:
    """Best-effort removal of Windows Hidden attribute after final rename."""
    attrs = get_windows_file_attributes(path)
    if attrs is None:
        return
    if attrs & FILE_ATTRIBUTE_HIDDEN:
        set_windows_file_attributes(path, attrs & ~FILE_ATTRIBUTE_HIDDEN)


@dataclass(frozen=True)
class RenameOptions:
    mode: str
    start_number: int = 1
    episode_output: str = "episode_only"
    title: str = ""
    number_width: int = 1
    template: str = ""
    keep_extension_case: bool = False


@dataclass
class RenameItem:
    old_path: Path
    new_path: Path
    episode_number: int | None = None
    status: str = READY_STATUS
    error_type: str = ""

    @property
    def ok(self) -> bool:
        return self.status == READY_STATUS


@dataclass
class RenamePlan:
    root: Path
    options: RenameOptions
    items: list[RenameItem]
    errors: list[str] = field(default_factory=list)
    cleanup_dirs: list[Path] = field(default_factory=list)
    log_path: Path | None = None

    @property
    def can_execute(self) -> bool:
        return bool(self.items) and not self.errors and all(item.ok for item in self.items)


@dataclass
class ExecuteResult:
    success: bool
    renamed: list[RenameItem] = field(default_factory=list)
    deleted_folders: list[Path] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    log_path: Path | None = None


ProgressCallback = Callable[[int, int, str], None]


def _format_number(value: int, width: int) -> str:
    if width <= 1:
        return str(value)
    return f"{value:0{width}d}"


def _extension_for(path: Path, options: RenameOptions) -> str:
    return path.suffix if options.keep_extension_case else path.suffix.lower()


def _has_invalid_filename_chars(text: str) -> bool:
    return bool(INVALID_FILENAME_RE.search(text))


def _validate_filename_part(text: str, label: str) -> str | None:
    if text != text.strip():
        return f"{label}不能包含首尾空格"
    if _has_invalid_filename_chars(text):
        return f"{label}包含非法文件名字符"
    return None


def _template_fields(template: str) -> set[str]:
    fields: set[str] = set()
    for _literal, field_name, _format_spec, _conversion in string.Formatter().parse(template):
        if field_name:
            fields.add(field_name.split(".")[0].split("[")[0])
    return fields


def _default_template(options: RenameOptions) -> str:
    if options.template:
        return options.template
    if options.mode == "sequential":
        return "{number}"
    if options.episode_output == "title":
        return "{title}-第{episode}集"
    return "第{episode}集"


def _validate_options(options: RenameOptions) -> list[str]:
    errors: list[str] = []
    if options.mode not in {"sequential", "episode"}:
        errors.append("未知模式")
    if options.mode == "sequential" and options.start_number < 0:
        errors.append("起始数字不能小于 0")
    if options.number_width not in {1, 2, 3}:
        errors.append("补零位数只能是 1、2 或 3")
    if options.title:
        title_error = _validate_filename_part(options.title, "剧名")
        if title_error:
            errors.append(title_error)

    template = _default_template(options)
    if not template.strip():
        errors.append("命名模板不能为空")
    template_error = _validate_filename_part(template, "命名模板")
    if template_error:
        errors.append(template_error)

    fields = _template_fields(template)
    allowed = {"number", "episode", "title"}
    unknown_fields = fields - allowed
    if unknown_fields:
        errors.append(f"命名模板包含未知字段：{', '.join(sorted(unknown_fields))}")
    if "{title}" in template and not options.title:
        errors.append("命名模板使用了剧名，但剧名不能为空")

    if options.mode == "sequential":
        if fields & {"episode", "title"}:
            errors.append("顺序模式的模板只能使用 {number}")
    elif options.mode == "episode":
        if "episode" not in fields:
            errors.append("集数模式的模板必须包含 {episode}")
        if options.episode_output not in {"episode_only", "title"}:
            errors.append("未知集数输出格式")
        if options.episode_output == "title" and not options.title.strip():
            errors.append("剧名不能为空")
    return errors


def build_rename_plan(root: str | Path, options: RenameOptions) -> RenamePlan:
    root_path = Path(root).resolve()
    errors = _validate_options(options)
    if not root_path.exists() or not root_path.is_dir():
        errors.append("请选择有效文件夹")
        return RenamePlan(root_path, options, [], errors)

    videos = find_videos(root_path)
    if not videos:
        errors.append("未找到支持的视频文件")
        return RenamePlan(root_path, options, [], errors)

    if options.mode == "sequential":
        items = _build_sequential_items(root_path, videos, options)
    else:
        items = _build_episode_items(root_path, videos, options)

    cleanup_dirs = sorted({path.parent for path in videos if path.parent.resolve() != root_path})
    _mark_conflicts(items)
    if errors:
        for item in items:
            if item.ok:
                item.status = "配置错误"
                item.error_type = "config"
    return RenamePlan(
        root_path,
        options,
        items,
        errors,
        cleanup_dirs=cleanup_dirs,
        log_path=make_log_path(root_path),
    )


def build_rename_plans(
    roots: list[str | Path],
    options: RenameOptions,
    continuous_numbering: bool = False,
) -> list[RenamePlan]:
    """为多个文件夹各自生成改名计划。

    continuous_numbering 仅对“连续编号”模式生效：勾选后多个文件夹接续编号，
    例如 A 有 3 个视频 → 1、2、3，B 接着 → 4、5；文件仍各自留在自己的文件夹里。
    """
    plans: list[RenamePlan] = []
    running_start = options.start_number
    use_continuous = continuous_numbering and options.mode == "sequential"
    for root in roots:
        current_options = replace(options, start_number=running_start) if use_continuous else options
        plan = build_rename_plan(root, current_options)
        plans.append(plan)
        if use_continuous:
            running_start += len(plan.items)
    return plans


def _build_sequential_items(root: Path, videos: list[Path], options: RenameOptions) -> list[RenameItem]:
    ordered = sorted(videos, key=lambda p: natural_sort_key(p.relative_to(root)))
    items: list[RenameItem] = []
    template = _default_template(options)
    for offset, old_path in enumerate(ordered):
        number = options.start_number + offset
        stem = _render_template(template, options, number=number, episode=None)
        new_path = root / f"{stem}{_extension_for(old_path, options)}"
        items.append(_make_item(old_path, new_path))
    return items


def _build_episode_items(root: Path, videos: list[Path], options: RenameOptions) -> list[RenameItem]:
    parsed_items: list[RenameItem] = []
    template = _default_template(options)
    for old_path in videos:
        episode = parse_episode_number(old_path.name)
        if episode is None:
            parsed_items.append(
                RenameItem(
                    old_path=old_path,
                    new_path=root / old_path.name,
                    episode_number=None,
                    status="无法识别集数",
                    error_type="parse",
                )
            )
            continue
        stem = _render_template(template, options, number=None, episode=episode)
        new_path = root / f"{stem}{_extension_for(old_path, options)}"
        parsed_items.append(_make_item(old_path, new_path, episode))

    return sorted(
        parsed_items,
        key=lambda item: (
            item.episode_number is None,
            item.episode_number if item.episode_number is not None else 10**9,
            natural_sort_key(item.old_path.relative_to(root)),
        ),
    )


def _render_template(template: str, options: RenameOptions, number: int | None, episode: int | None) -> str:
    values = {
        "number": "" if number is None else _format_number(number, options.number_width),
        "episode": "" if episode is None else _format_number(episode, options.number_width),
        "title": options.title,
    }
    return template.format(**values)


def _make_item(old_path: Path, new_path: Path, episode_number: int | None = None) -> RenameItem:
    status = READY_STATUS
    error_type = ""
    if _has_invalid_filename_chars(new_path.name):
        status = "目标文件名包含非法字符"
        error_type = "invalid_filename"
    elif new_path.name != new_path.name.strip():
        status = "目标文件名包含首尾空格"
        error_type = "invalid_filename"
    elif _is_reserved_filename(new_path.name):
        status = "目标文件名为系统保留名"
        error_type = "reserved_name"
    return RenameItem(old_path=old_path, new_path=new_path, episode_number=episode_number, status=status, error_type=error_type)


def _same_file(path_a: Path, path_b: Path) -> bool:
    try:
        return path_a.resolve() == path_b.resolve() or path_a.samefile(path_b)
    except FileNotFoundError:
        return path_a.resolve() == path_b.resolve()


def _mark_conflicts(items: list[RenameItem]) -> None:
    target_to_items: dict[str, list[RenameItem]] = {}
    for item in items:
        key = str(item.new_path.resolve()).casefold()
        target_to_items.setdefault(key, []).append(item)

    for same_target_items in target_to_items.values():
        if len(same_target_items) > 1:
            for item in same_target_items:
                if item.ok:
                    item.status = "目标文件名重复"
                    item.error_type = "duplicate_target"

    old_paths = {str(item.old_path.resolve()).casefold() for item in items}
    for item in items:
        if not item.ok:
            continue
        if item.new_path.exists():
            new_key = str(item.new_path.resolve()).casefold()
            if new_key in old_paths and _same_file(item.old_path, item.new_path):
                continue
            item.status = "目标已存在"
            item.error_type = "target_exists"


def execute_rename_plan(plan: RenamePlan, progress_callback: ProgressCallback | None = None) -> ExecuteResult:
    log_path = _unique_log_path(plan.log_path or make_log_path(plan.root))
    if not plan.can_execute:
        _write_log_for_items(log_path, plan.items, {}, ["预览存在错误，已禁止执行"] + plan.errors)
        return ExecuteResult(False, errors=["预览存在错误，已禁止执行"] + plan.errors, log_path=log_path)

    runtime_errors = _runtime_target_conflicts(plan.items)
    if runtime_errors:
        _write_log_for_items(log_path, plan.items, {}, runtime_errors)
        return ExecuteResult(False, errors=runtime_errors, log_path=log_path)

    token = uuid.uuid4().hex
    staged: list[tuple[Path, Path, RenameItem]] = []
    completed: list[RenameItem] = []
    item_errors: dict[Path, str] = {}
    total_steps = len(plan.items) * 2
    step = 0

    try:
        for index, item in enumerate(plan.items):
            progress_callback and progress_callback(step, total_steps, f"暂存：{item.old_path.name}")
            temp_path = make_staging_path(item.old_path, token, index)
            while temp_path.exists():
                temp_path = make_staging_path(item.old_path, uuid.uuid4().hex, index)
            move_no_overwrite(item.old_path, temp_path)
            staged.append((temp_path, item.old_path, item))
            step += 1

        runtime_errors = _runtime_target_conflicts_after_staging(plan.items)
        if runtime_errors:
            raise FileExistsError("；".join(runtime_errors))

        for temp_path, _original_path, item in staged:
            progress_callback and progress_callback(step, total_steps, f"写入：{item.new_path.name}")
            move_no_overwrite(temp_path, item.new_path)
            clear_windows_hidden_attribute(item.new_path)
            completed.append(item)
            step += 1

        deleted = delete_empty_source_folders(plan.root, plan.cleanup_dirs)
        progress_callback and progress_callback(total_steps, total_steps, "完成")
        _write_log_for_items(log_path, plan.items, {item.old_path: "success" for item in completed}, item_errors)
        return ExecuteResult(True, renamed=completed, deleted_folders=deleted, log_path=log_path)
    except Exception as exc:
        errors = [f"执行失败：{exc}"]
        _rollback_completed(completed, errors)
        _rollback_staged(staged, errors)
        for item in plan.items:
            item_errors[item.old_path] = errors[0]
        _write_log_for_items(log_path, plan.items, {item.old_path: "rolled_back" for item in completed}, item_errors)
        return ExecuteResult(False, renamed=completed, errors=errors, log_path=log_path)


def _runtime_target_conflicts(items: list[RenameItem]) -> list[str]:
    old_paths = {str(item.old_path.resolve()).casefold() for item in items}
    errors: list[str] = []
    for item in items:
        if item.new_path.exists():
            new_key = str(item.new_path.resolve()).casefold()
            if new_key in old_paths:
                continue
            errors.append(f"目标已存在：{item.new_path}")
    return errors


def _runtime_target_conflicts_after_staging(items: list[RenameItem]) -> list[str]:
    errors: list[str] = []
    for item in items:
        if item.new_path.exists():
            errors.append(f"目标已存在：{item.new_path}")
    return errors


def _rollback_completed(completed: list[RenameItem], errors: list[str]) -> None:
    for item in reversed(completed):
        if item.new_path.exists() and not item.old_path.exists():
            try:
                move_no_overwrite(item.new_path, item.old_path)
            except Exception as rollback_exc:
                errors.append(f"回滚失败：{item.old_path}：{rollback_exc}")


def _rollback_staged(staged: list[tuple[Path, Path, RenameItem]], errors: list[str]) -> None:
    for temp_path, original_path, _item in reversed(staged):
        if temp_path.exists() and not original_path.exists():
            try:
                move_no_overwrite(temp_path, original_path)
            except Exception as rollback_exc:
                errors.append(f"回滚失败：{original_path}：{rollback_exc}")


def _unique_log_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    for index in range(1, 1000):
        candidate = path.with_name(f"{stem}_{index}{suffix}")
        if not candidate.exists():
            return candidate
    return path.with_name(f"{stem}_{uuid.uuid4().hex}{suffix}")


def _write_log_for_items(
    log_path: Path,
    items: list[RenameItem],
    statuses: dict[Path, str],
    errors: dict[Path, str] | list[str],
) -> None:
    rows: list[dict[str, object]] = []
    shared_error = "；".join(errors) if isinstance(errors, list) else ""
    for item in items:
        status = statuses.get(item.old_path, "failed" if errors else "pending")
        error = errors.get(item.old_path, "") if isinstance(errors, dict) else shared_error
        size, modified_time = file_metadata(item.new_path if item.new_path.exists() else item.old_path)
        rows.append(
            {
                "original_path": item.old_path,
                "new_path": item.new_path,
                "status": status,
                "error": error,
                "size": size,
                "modified_time": modified_time,
            }
        )
    write_operation_log(log_path, rows)
