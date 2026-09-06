"""Section 9 vs. the reference DOCX table23."""


def test_fraud_top10_matches_reference(july_report):
    got = [(r.phone, r.unique_devices) for r in july_report.fraud_rows]
    expected = [
        ("79775298728", 160), ("9653012721", 58), ("79111233223", 26),
        ("89967425036", 19), ("79604705427", 15), ("74999684058", 15),
        ("79521438515", 14), ("79094095777", 14), ("79033812943", 13),
        ("79130672260", 12),
    ]
    assert got == expected
