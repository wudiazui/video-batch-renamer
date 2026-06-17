from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


APP_DIR_NAME = "VideoRenamerGUI"
SETTINGS_FILE_NAME = "settings.json"


def get_settings_path() -> Path:
    base = os.environ.get("APPDATA")
    if base:
        return Path(base) / APP_DIR_NAME / SETTINGS_FILE_NAME
    return Path.home() / APP_DIR_NAME / SETTINGS_FILE_NAME


def load_settings(path: str | Path | None = None) -> dict[str, Any]:
    settings_path = Path(path) if path is not None else get_settings_path()
    if not settings_path.exists():
        return {}
    try:
        with settings_path.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_settings(settings: dict[str, Any], path: str | Path | None = None) -> Path:
    settings_path = Path(path) if path is not None else get_settings_path()
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    with settings_path.open("w", encoding="utf-8") as file:
        json.dump(settings, file, ensure_ascii=False, indent=2)
    return settings_path
