"""Dedicated test for spec §1.3: multi-part continuation rows must NOT be
deduplicated by №п/п, and their blank categorical fields must surface under
explicit placeholder labels rather than vanishing or merging into the
"parent" repair row's ASC/region/manufacturer/brand.
"""
from repair_report.ingest.excel_reader import load_repair_data
from repair_report.analytics.common import fill_missing_categoricals
from tests.conftest import JULY_ONLY_FILE


def test_continuation_row_190_gets_placeholder_labels_not_dropped():
    df, _ = load_repair_data(str(JULY_ONLY_FILE))
    # Row #190 (spare-part continuation line for a washing-machine repair,
    # see docs/REVERSE_ENGINEERING.md and the product spec §1.3) has a blank
    # 'Наименование АСЦ'/'Регион АСЦ'/etc, but real data in 'Артикул'.
    continuation = df[(df["row_no"] == 190) & (df["asc_name"].isna())]
    assert len(continuation) == 1
    assert continuation.iloc[0]["part_article"] == "WashMotorWhite"

    filled = fill_missing_categoricals(df)
    row = filled[(filled["row_no"] == 190) & filled["asc_name"].isna()].iloc[0]
    assert row["asc_name_grp"] == "Неизвестный АСЦ"
    assert row["asc_region_grp"] == "—"

    # It must still count as its own repair-record unit -- not merged with
    # the sibling "parent" row that shares the same №п/п.
    same_row_no = df[df["row_no"] == 190]
    assert len(same_row_no) == 2  # the real repair row + this continuation row


def test_asc_count_includes_unknown_placeholder_bucket(july_report):
    # 104 = 103 real ASC names + 1 "Неизвестный АСЦ" bucket (§1.3, verified
    # against the reference report's headline figures).
    assert july_report.kpis.asc_count == 104
    assert july_report.kpis.region_count == 67
    assert july_report.kpis.manufacturer_count == 10
    assert july_report.kpis.repair_count == 1535
