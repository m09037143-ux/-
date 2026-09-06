"""Loads the JSON config files shipped under repair_report/config/.

Works both when running from source and when frozen by PyInstaller (which
exposes extra data files relative to sys._MEIPASS).
"""
from __future__ import annotations

import json
import sys
from functools import lru_cache
from pathlib import Path


def config_dir() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "repair_report" / "config"  # type: ignore[attr-defined]
    return Path(__file__).parent


@lru_cache(maxsize=None)
def _load_json(filename: str) -> dict:
    path = config_dir() / filename
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def category_mapping() -> dict:
    return _load_json("category_mapping.json")


def repair_level_labels() -> dict:
    return _load_json("repair_level_labels.json")


def app_settings() -> dict:
    return _load_json("app_settings.json")


def iris_rules() -> dict:
    return _load_json("iris_rules.json")


def clear_cache() -> None:
    """For tests that swap config files at runtime."""
    _load_json.cache_clear()
