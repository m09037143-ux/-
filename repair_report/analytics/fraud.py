"""Section 9 -- Контроль Фрода и СБ. Spec §2.12."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from repair_report.config import loader


@dataclass
class FraudRow:
    phone: str
    client: str
    unique_devices: int


def fraud_table(current_df: pd.DataFrame, top_n: int | None = None, min_unique_devices: int | None = None) -> list[FraudRow]:
    settings = loader.app_settings()["fraud"]
    top_n = top_n or loader.app_settings()["top_n_fraud"]
    min_unique_devices = min_unique_devices if min_unique_devices is not None else settings["min_unique_devices"]

    df = current_df[current_df["client_phone"].notna() & current_df["serial_number"].notna()].copy()
    df["phone_str"] = df["client_phone"].apply(lambda v: str(int(v)) if float(v).is_integer() else str(v))

    rows = []
    for phone, g in df.groupby("phone_str", sort=False):
        unique_devices = g["serial_number"].nunique()
        if unique_devices < min_unique_devices:
            continue
        clients = list(dict.fromkeys(c for c in g["client_data"].tolist() if pd.notna(c) and str(c).strip()))
        rows.append(FraudRow(phone=phone, client=", ".join(clients), unique_devices=unique_devices))

    rows.sort(key=lambda r: r.unique_devices, reverse=True)
    return rows[:top_n]
