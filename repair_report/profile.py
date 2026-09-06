"""Client profile persistence (spec §2.3): the report header's static
requisites (Исполнитель/Заказчик/Договор/подписант) are entered once per
client and reused every month, rather than re-typed or read from the .xlsx
(which never contains them).

Stored as JSON under the user's per-app data directory so profiles survive
across app updates and don't require write access to the install directory
(important for a PyInstaller --onefile build installed system-wide).
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class ClientProfile:
    name: str  # profile label shown in the picker, e.g. "АО «Бытовая Электроника»"
    executor: str = ""  # Исполнитель
    customer: str = ""  # Заказчик
    contract_number: str = ""  # Договор №
    contract_date: str = ""  # Договор от ДД.ММ.ГГГГ
    signatory_title: str = ""  # e.g. "Генеральный директор ООО «Морозко»"
    signatory_name: str = ""  # e.g. "А.В. Костерев"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ClientProfile":
        return cls(**{k: d.get(k, "") for k in cls.__dataclass_fields__})


def app_data_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    d = Path(base) / "RepairReportApp" / "profiles"
    d.mkdir(parents=True, exist_ok=True)
    return d


def list_profiles() -> list[str]:
    return sorted(p.stem for p in app_data_dir().glob("*.json"))


def load_profile(name: str) -> ClientProfile:
    path = app_data_dir() / f"{name}.json"
    with open(path, encoding="utf-8") as f:
        return ClientProfile.from_dict(json.load(f))


def save_profile(profile: ClientProfile) -> None:
    path = app_data_dir() / f"{profile.name}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(profile.to_dict(), f, ensure_ascii=False, indent=2)


def delete_profile(name: str) -> None:
    path = app_data_dir() / f"{name}.json"
    if path.exists():
        path.unlink()
