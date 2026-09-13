"""Tests for the optional AI-summary feature (repair_report/ai/,
ai_settings.py). No real network call is made anywhere here -- see
docs/REVERSE_ENGINEERING.md §16 for why the actual Yandex Cloud endpoint
could not be exercised in this environment; request_completion's HTTP
call is mocked at the urllib level, and response parsing is tested
against the documented/likely response shapes directly.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from repair_report.ai.client import AIRequestError, AIResponseError, _extract_text, request_completion
from repair_report.ai.summary import build_digest, generate_summary
from repair_report.ai_settings import AiSettings


def test_ai_settings_effective_model_defaults_from_folder_id():
    s = AiSettings(api_key="k", folder_id="abc123")
    assert s.effective_model() == "gpt://abc123/aliceai-llm/latest"
    assert s.is_configured()


def test_ai_settings_effective_model_prefers_explicit_model():
    s = AiSettings(api_key="k", folder_id="abc123", model="gpt://abc123/custom/v2")
    assert s.effective_model() == "gpt://abc123/custom/v2"


def test_ai_settings_not_configured_without_key_or_folder():
    assert not AiSettings().is_configured()
    assert not AiSettings(api_key="k").is_configured()
    assert not AiSettings(folder_id="f").is_configured()


def test_extract_text_output_text_convenience_field():
    assert _extract_text({"output_text": "  Готовое резюме.  "}) == "Готовое резюме."


def test_extract_text_responses_api_shape():
    data = {
        "output": [
            {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Первый абзац."}]},
        ]
    }
    assert _extract_text(data) == "Первый абзац."


def test_extract_text_chat_completions_fallback_shape():
    data = {"choices": [{"message": {"content": "Ответ в стиле chat completions."}}]}
    assert _extract_text(data) == "Ответ в стиле chat completions."


def test_extract_text_raises_on_unknown_shape():
    with pytest.raises(AIResponseError):
        _extract_text({"something_else": 1})


def test_build_digest_never_includes_detail_rows_or_pii():
    """Product decision: only aggregated figures go to the external AI
    service -- never per-repair rows, customer names, serials, or phones."""
    report = MagicMock()
    report.current_span = "июль 2026"
    report.previous_available = False
    report.previous_span = None
    report.current_span_complete = True
    report.kpis = MagicMock(
        repair_count=100, total_sum=200000.0, avg_check=2000.0, avg_duration_days=5.0,
        asc_count=10, region_count=5, manufacturer_count=3, brand_count=4, model_count=20,
        visits_count=2, visits_sum=1000.0, parts_count=1, parts_sum=500.0,
    )
    report.monthly_dynamics = None
    report.equipment_by_sum = MagicMock(rows=[])
    report.asc_leader_follower = None
    report.golden_standard = []
    report.risk_zone = MagicMock(rows=[], network_anr_share_pct=0)
    report.doa_total = 0
    report.fraud_rows = []
    report.parts = None
    report.support = None

    digest = build_digest(report)
    assert "100" in digest  # repair_count shows up
    # No detail-row-shaped content (phone numbers, serials) should ever be
    # constructed here since build_digest never touches report.detail_rows.
    assert "detail_rows" not in digest


@patch("repair_report.ai.client.urllib.request.urlopen")
def test_request_completion_success(mock_urlopen):
    resp = MagicMock()
    resp.read.return_value = json.dumps({"output_text": "Резюме."}).encode("utf-8")
    resp.__enter__.return_value = resp
    mock_urlopen.return_value = resp

    text = request_completion(
        api_key="key", folder_id="folder", model="gpt://folder/aliceai-llm/latest",
        instructions="сис", input_text="данные",
    )
    assert text == "Резюме."
    # Sanity-check the request shape matches the user-supplied curl example.
    sent_req = mock_urlopen.call_args[0][0]
    assert sent_req.full_url == "https://ai.api.cloud.yandex.net/v1/responses"
    assert sent_req.get_header("Authorization") == "Api-Key key"
    assert sent_req.get_header("Openai-project") == "folder"
    body = json.loads(sent_req.data)
    assert body["model"] == "gpt://folder/aliceai-llm/latest"
    assert body["input"] == "данные"


@patch("repair_report.ai.client.urllib.request.urlopen")
def test_request_completion_http_error_raises_ai_request_error(mock_urlopen):
    import urllib.error

    mock_urlopen.side_effect = urllib.error.HTTPError(
        "url", 401, "Unauthorized", hdrs=None, fp=MagicMock(read=lambda: b"bad key")
    )
    with pytest.raises(AIRequestError):
        request_completion(api_key="bad", folder_id="f", model="m", instructions="", input_text="x")


@patch("repair_report.ai.client.urllib.request.urlopen")
def test_request_completion_handshake_timeout_hints_at_vpn(mock_urlopen):
    """Confirmed in the field (docs/REVERSE_ENGINEERING.md §16): a VPN/
    corporate proxy stalling the TLS handshake is the most common cause of
    this exact error -- the message should point users at that first."""
    import urllib.error

    mock_urlopen.side_effect = urllib.error.URLError("_ssl.c:989: The handshake operation timed out")
    with pytest.raises(AIRequestError, match="VPN"):
        request_completion(api_key="k", folder_id="f", model="m", instructions="", input_text="x")


@patch("repair_report.ai.summary.request_completion")
def test_generate_summary_truncates_overlong_output(mock_request):
    mock_request.return_value = "слово " * 5000  # way over MAX_SUMMARY_CHARS
    report = MagicMock()
    report.current_span = "2026 год"
    report.previous_available = False
    report.current_span_complete = True
    report.kpis = MagicMock(
        repair_count=1, total_sum=1.0, avg_check=1.0, avg_duration_days=None,
        asc_count=1, region_count=1, manufacturer_count=1, brand_count=1, model_count=1,
        visits_count=0, visits_sum=0.0, parts_count=0, parts_sum=0.0,
    )
    report.monthly_dynamics = None
    report.equipment_by_sum = MagicMock(rows=[])
    report.asc_leader_follower = None
    report.golden_standard = []
    report.risk_zone = MagicMock(rows=[], network_anr_share_pct=0)
    report.doa_total = 0
    report.fraud_rows = []
    report.parts = None
    report.support = None

    settings = AiSettings(api_key="k", folder_id="f")
    text = generate_summary(report, settings)
    assert len(text) <= 11_100  # MAX_SUMMARY_CHARS plus the truncation note
    assert text.endswith("[Резюме обрезано до объёма ~2 страниц А4.]")


def test_generate_summary_refuses_when_not_configured():
    from repair_report.ai.client import AIRequestError

    report = MagicMock()
    with pytest.raises(AIRequestError):
        generate_summary(report, AiSettings())
