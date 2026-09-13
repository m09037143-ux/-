"""ИИ (AI summary) settings persistence -- same pattern as profile.py:
JSON under the user's local app-data directory, entered once via the
"Настройки ИИ" dialog and reused every run.

SECURITY NOTE: this file contains an API key in PLAIN TEXT. That mirrors
how ClientProfile's own data is stored (no secret-management layer exists
in this desktop app), and -- critically -- it lives under the user's
per-app LOCALAPPDATA/XDG data directory, NEVER inside the repository or
anywhere PyInstaller bundles into the .exe. Nothing under repair_report/
ever hardcodes a real key; if you are looking at a committed file with an
API key in it, that is a mistake to fix immediately, not a pattern to
extend. See docs/REVERSE_ENGINEERING.md §16.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class AiSettings:
    api_key: str = ""
    folder_id: str = ""  # Yandex Cloud folder id (the "OpenAI-Project" header value)
    model: str = ""  # e.g. "gpt://<folder_id>/aliceai-llm/latest" -- derived from folder_id if left blank
    enabled_by_default: bool = False  # whether the "Провести анализ с помощью ИИ" checkbox starts checked

    def effective_model(self) -> str:
        return self.model.strip() or f"gpt://{self.folder_id}/aliceai-llm/latest"

    def is_configured(self) -> bool:
        return bool(self.api_key.strip() and self.folder_id.strip())

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> AiSettings:
        return cls(**{k: d.get(k, getattr(cls, k, "")) for k in cls.__dataclass_fields__})


def _settings_path() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    d = Path(base) / "RepairReportApp"
    d.mkdir(parents=True, exist_ok=True)
    return d / "ai_settings.json"


def load_ai_settings() -> AiSettings:
    path = _settings_path()
    if not path.exists():
        return AiSettings()
    with open(path, encoding="utf-8") as f:
        return AiSettings.from_dict(json.load(f))


def save_ai_settings(settings: AiSettings) -> None:
    path = _settings_path()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(settings.to_dict(), f, ensure_ascii=False, indent=2)
    if sys.platform != "win32":
        try:
            os.chmod(path, 0o600)  # best-effort: keep the API key from being world-readable
        except OSError:
            pass
