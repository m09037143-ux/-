"""Column-resolution robustness (spec §5: named-column matching, not by index)."""
import pandas as pd
import pytest

from repair_report.ingest.excel_reader import ColumnResolutionError, load_repair_data
from tests.conftest import JULY_ONLY_FILE


def test_loads_all_critical_columns_without_fuzzy_fallback():
    df, report = load_repair_data(str(JULY_ONLY_FILE))
    assert report.ok
    assert report.missing_critical == []
    assert report.fuzzy_matched == {}
    assert len(df) == 1535


def test_missing_critical_column_raises_clear_error(tmp_path):
    df = pd.read_excel(str(JULY_ONLY_FILE), sheet_name="Table")
    df = df.drop(columns=["Наименование АСЦ"])
    bad_file = tmp_path / "broken.xlsx"
    df.to_excel(bad_file, sheet_name="Table", index=False)

    with pytest.raises(ColumnResolutionError) as exc_info:
        load_repair_data(str(bad_file))
    assert "Наименование АСЦ" in str(exc_info.value)


def test_renamed_column_still_resolves_via_fuzzy_match(tmp_path):
    df = pd.read_excel(str(JULY_ONLY_FILE), sheet_name="Table")
    df = df.rename(columns={"Наименование АСЦ": "наименование асц "})  # cosmetic whitespace/case change
    renamed_file = tmp_path / "renamed.xlsx"
    df.to_excel(renamed_file, sheet_name="Table", index=False)

    loaded, report = load_repair_data(str(renamed_file))
    assert report.ok
    assert "asc_name" in loaded.columns
