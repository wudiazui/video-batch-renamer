from __future__ import annotations

import csv
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


LOG_DIR_NAME = "_rename_logs"
READY_STATUS = "准备就绪"
LOG_FIELDS = [
    "timestamp",
    "original_path",
    "new_path",
    "status",
    "error",
    "size",
    "modified_time",
]


@dataclass
class UndoItem:
    old_path: Path
    new_path: Path
    status: str = READY_STATUS

    @property
    def ok(self) -> bool:
        return self.status == READY_STATUS


@dataclass
class UndoPlan:
    log_path: Path
    items: list[UndoItem]
    errors: list[str] = field(default_factory=list)

    @property
    def can_execute(self) -> bool:
        return bool(self.items) and not self.errors and all(item.ok for item in self.items)


@dataclass
class UndoResult:
    success: bool
    items: list[UndoItem] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def make_log_path(root: str | Path, now: datetime | None = None) -> Path:
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    return Path(root).resolve() / LOG_DIR_NAME / f"rename_log_{stamp}.csv"


def find_latest_log(root: str | Path) -> Path | None:
    log_dir = Path(root).resolve() / LOG_DIR_NAME
    if not log_dir.exists():
        return None
    logs = sorted(log_dir.glob("rename_log_*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    return logs[0] if logs else None


def write_operation_log(log_path: str | Path, rows: list[dict[str, object]]) -> Path:
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().isoformat(timespec="seconds")
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=LOG_FIELDS)
        writer.writeheader()
        for row in rows:
            written = {field: row.get(field, "") for field in LOG_FIELDS}
            if not written.get("timestamp"):
                written["timestamp"] = stamp
            writer.writerow(written)
    return path


def file_metadata(path: str | Path) -> tuple[str, str]:
    target = Path(path)
    try:
        stat = target.stat()
    except OSError:
        return "", ""
    modified = datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds")
    return str(stat.st_size), modified


def _read_success_rows(log_path: Path) -> list[dict[str, str]]:
    with log_path.open("r", encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    return [row for row in rows if row.get("status") == "success"]


def _paths_same(a: Path, b: Path) -> bool:
    """两个路径是否指向同一文件（忽略大小写 / 8.3 短名等差异）。"""
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return str(a).casefold() == str(b).casefold()


def build_undo_plan(log_path: str | Path) -> UndoPlan:
    path = Path(log_path)
    errors: list[str] = []
    if not path.exists():
        return UndoPlan(path, [], ["日志文件不存在"])

    items: list[UndoItem] = []
    try:
        rows = _read_success_rows(path)
    except (OSError, csv.Error) as exc:
        return UndoPlan(path, [], [f"读取日志失败：{exc}"])

    for row in rows:
        source = Path(row.get("new_path", ""))
        target = Path(row.get("original_path", ""))
        status = READY_STATUS
        if not source.exists():
            status = "当前文件不存在"
        elif target.exists() and not _paths_same(source, target):
            # 改名是空操作（新名==原名）时 target 就是 source 自己，不算冲突。
            status = "目标已存在"
        items.append(UndoItem(old_path=source, new_path=target, status=status))

    if not items:
        errors.append("日志中没有可撤销的成功记录")
    return UndoPlan(path, items, errors)


def move_no_overwrite(source: str | Path, target: str | Path) -> None:
    source_path = Path(source)
    target_path = Path(target)
    if target_path.exists():
        raise FileExistsError(f"目标已存在：{target_path}")
    target_path.parent.mkdir(parents=True, exist_ok=True)
    # shutil.move 同卷时走原子 rename，跨卷（不同盘符/网络映射）时自动回退复制+删除。
    # 上面已确认目标不存在，因此不会覆盖任何文件。
    shutil.move(str(source_path), str(target_path))


def execute_undo_plan(plan: UndoPlan) -> UndoResult:
    if not plan.can_execute:
        return UndoResult(False, errors=["撤销预览存在错误，已禁止执行"] + plan.errors)

    completed: list[UndoItem] = []
    errors: list[str] = []
    try:
        for item in plan.items:
            if not _paths_same(item.old_path, item.new_path):
                move_no_overwrite(item.old_path, item.new_path)
            completed.append(item)
    except Exception as exc:
        errors.append(f"撤销失败：{exc}")
        for item in reversed(completed):
            if item.new_path.exists() and not item.old_path.exists():
                try:
                    move_no_overwrite(item.new_path, item.old_path)
                except Exception as rollback_exc:
                    errors.append(f"撤销回滚失败：{item.old_path}：{rollback_exc}")
        return UndoResult(False, items=completed, errors=errors)
    return UndoResult(True, items=completed)
