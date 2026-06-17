
from __future__ import annotations

import re
from pathlib import Path

VIDEO_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".mkv",
    ".avi",
    ".wmv",
    ".flv",
    ".webm",
    ".m4v",
    ".ts",
}


def natural_sort_key(value: str | Path) -> list[object]:
    """Return a human-friendly sort key: 2 comes before 10."""
    text = str(value).casefold()
    parts = re.split(r"(\d+)", text)
    return [int(part) if part.isdigit() else part for part in parts]


def is_video_file(path: Path) -> bool:
    return path.is_file() and path.suffix.casefold() in VIDEO_EXTENSIONS


def find_videos(root: str | Path) -> list[Path]:
    """Recursively find supported video files under root, sorted naturally by relative path."""
    root_path = Path(root).resolve()
    videos = [path for path in root_path.rglob("*") if is_video_file(path)]
    return sorted(videos, key=lambda p: natural_sort_key(p.relative_to(root_path)))


def delete_empty_source_folders(root: str | Path, source_dirs: list[Path] | set[Path]) -> list[Path]:
    """Delete only folders related to files moved by the current operation."""
    root_path = Path(root).resolve()
    candidates: set[Path] = set()
    for source_dir in source_dirs:
        current = Path(source_dir).resolve()
        while current != root_path and root_path in current.parents:
            candidates.add(current)
            current = current.parent

    deleted: list[Path] = []
    for folder in sorted(candidates, key=lambda p: len(p.relative_to(root_path).parts), reverse=True):
        try:
            folder.rmdir()
        except OSError:
            continue
        else:
            deleted.append(folder)
    return deleted
