"""Section 5 -- Эффективность АСЦ, Качество и SLA. Spec §2.8.

5.1 Golden standard, 5.2 Risk zone, 5.3 repair duration, 5.4 DOA.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from repair_report.analytics.common import fill_missing_categoricals, repair_level_code, safe_share_pct
from repair_report.config import loader

IRIS_FIELDS = ["iris_section_code", "iris_defect_code", "iris_repair_code"]


@dataclass
class GoldenStandardRow:
    asc_name: str
    claims: int
    avg_check: float
    anr_share_pct: float


def golden_standard(current_df: pd.DataFrame) -> list[GoldenStandardRow]:
    settings = loader.app_settings()["golden_standard"]
    filled = fill_missing_categoricals(current_df).copy()
    filled["level_code"] = repair_level_code(filled)
    filled["is_anr"] = filled["level_code"].isin(loader.repair_level_labels().get("anr_codes", ["AN", "ANR"]))
    filled["iris_full"] = filled[IRIS_FIELDS].notna().all(axis=1)

    network_avg_check = filled["total_amount"].sum() / len(filled) if len(filled) else 0.0

    grp = filled.groupby("asc_name_grp").agg(
        claims=("total_amount", "size"),
        avg_check=("total_amount", "mean"),
        anr_share=("is_anr", "mean"),
        iris_full_share=("iris_full", "mean"),
    )
    qualifying = grp[
        (grp["claims"] >= settings["min_claims_threshold"])
        & (grp["avg_check"] < network_avg_check)
        & (grp["iris_full_share"] == 1.0)
        & (grp["anr_share"] * 100 < settings["max_anr_share_pct"])
    ]
    rows = [
        GoldenStandardRow(asc_name=str(idx), claims=int(r["claims"]), avg_check=float(r["avg_check"]), anr_share_pct=float(r["anr_share"] * 100))
        for idx, r in qualifying.sort_values("avg_check").iterrows()
    ]
    return rows


@dataclass
class RiskZoneRow:
    asc_name: str
    claims: int
    anr_share_pct: float


@dataclass
class RiskZoneResult:
    network_anr_share_pct: float
    rows: list[RiskZoneRow]


def risk_zone(current_df: pd.DataFrame) -> RiskZoneResult:
    settings = loader.app_settings()["risk_zone"]
    filled = fill_missing_categoricals(current_df).copy()
    filled["level_code"] = repair_level_code(filled)
    filled["is_anr"] = filled["level_code"].isin(loader.repair_level_labels().get("anr_codes", ["AN", "ANR"]))

    network_share = filled["is_anr"].mean() * 100 if len(filled) else 0.0
    threshold = network_share * settings["multiplier_over_network_avg"] / 100

    grp = filled.groupby("asc_name_grp").agg(claims=("total_amount", "size"), anr_share=("is_anr", "mean"))
    qualifying = grp[(grp["claims"] >= settings["min_claims_threshold"]) & (grp["anr_share"] > threshold)]
    qualifying = qualifying.sort_values("anr_share", ascending=False, kind="mergesort")
    rows = [RiskZoneRow(asc_name=str(idx), claims=int(r["claims"]), anr_share_pct=float(r["anr_share"] * 100)) for idx, r in qualifying.iterrows()]
    return RiskZoneResult(network_anr_share_pct=network_share, rows=rows)


@dataclass
class DurationExtreme:
    asc_name: str
    avg_days: float


@dataclass
class SlowRepairRow:
    asc_name: str
    brand: str
    model: str
    duration_days: int


@dataclass
class DurationAnalysis:
    fastest_asc: DurationExtreme | None
    slowest_asc: DurationExtreme | None
    top_slow_repairs: list[SlowRepairRow]


def duration_analysis(current_df: pd.DataFrame, top_n: int = 10) -> DurationAnalysis:
    filled = fill_missing_categoricals(current_df).copy()
    filled["duration_days"] = (filled["repair_date"] - filled["receipt_date"]).dt.days

    per_asc = filled.dropna(subset=["duration_days"]).groupby("asc_name_grp")["duration_days"].mean()
    fastest = slowest = None
    if len(per_asc):
        fastest = DurationExtreme(asc_name=str(per_asc.idxmin()), avg_days=float(per_asc.min()))
        slowest = DurationExtreme(asc_name=str(per_asc.idxmax()), avg_days=float(per_asc.max()))

    dated = filled.dropna(subset=["duration_days"]).sort_values("duration_days", ascending=False, kind="mergesort")
    top_rows = [
        SlowRepairRow(
            asc_name=str(r["asc_name_grp"]),
            brand=str(r["brand"]) if pd.notna(r["brand"]) else "",
            model=str(r["model"]) if pd.notna(r["model"]) else "",
            duration_days=int(r["duration_days"]),
        )
        for _, r in dated.head(top_n).iterrows()
    ]
    return DurationAnalysis(fastest_asc=fastest, slowest_asc=slowest, top_slow_repairs=top_rows)


@dataclass
class DoaRow:
    brand: str
    model: str
    count: int


def doa_table(current_df: pd.DataFrame, max_days: int | None = None, top_n: int = 15) -> tuple[list[DoaRow], int]:
    """Returns (top-N rows, total DOA row count). See §2.8/5.4:
    DOA = (Дата поступления - Дата продажи) in [0, max_days] days inclusive,
    both dates present. Verified exactly (all 15 top rows + counts) against
    the reference report's DOA table at max_days=30."""
    if max_days is None:
        max_days = loader.app_settings()["doa"]["max_days"]
    df = current_df.copy()
    diff = (df["receipt_date"] - df["sale_date"]).dt.days
    doa = df[df["sale_date"].notna() & (diff >= 0) & (diff <= max_days)]

    first_seen_order = list(dict.fromkeys(zip(doa["brand"], doa["model"])))
    rank = {k: i for i, k in enumerate(first_seen_order)}
    grp = doa.groupby(["brand", "model"]).size().reset_index(name="count")
    grp["rank"] = grp.apply(lambda r: rank.get((r["brand"], r["model"]), 0), axis=1)
    grp = grp.sort_values(["count", "rank"], ascending=[False, True], kind="mergesort")

    rows = [DoaRow(brand=str(r["brand"]), model=str(r["model"]), count=int(r["count"])) for _, r in grp.head(top_n).iterrows()]
    return rows, len(doa)
