"""Проверка гипотезы на лендинге: заявки на ранний доступ и воронка. Без базы данных."""

import uuid

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.models import EarlyAccessRequest, ProductEvent
from app.schemas.marketing import EarlyAccessIn, EventIn
from app.services import marketing_service

VID = "3f2b8c1e-aaaa-4bbb-8ccc-123456789abc"


def _lead(**over):
    data = {"name": "  Анна  ", "email": "Anna@Example.com", "consent": True, "visitor_id": VID}
    data.update(over)
    return EarlyAccessIn(**data)


# ---------------- воронка ----------------

def test_funnel_counts_unique_visitors_and_computes_conversion():
    f = marketing_service.build_funnel({"landing_view": 40, "cta_click": 10, "demo_click": 7, "lead_form_open": 8}, leads_total=3)
    assert (f.landing_visitors, f.cta_clicks, f.demo_clicks, f.form_opens, f.leads) == (40, 10, 7, 8, 3)
    assert f.cta_rate_pct == 25.0 and f.lead_rate_pct == 7.5


def test_funnel_without_visitors_has_zero_rates_not_a_division_error():
    f = marketing_service.build_funnel({}, leads_total=0)
    assert f.landing_visitors == 0 and f.cta_rate_pct == 0.0 and f.lead_rate_pct == 0.0
    assert marketing_service.build_funnel({"cta_click": 2}, leads_total=1).lead_rate_pct == 0.0  # заявка без просмотров не даёт бесконечности


# ---------------- проверка входных данных ----------------

def test_lead_requires_consent_a_name_and_a_real_email():
    assert _lead().name == "Анна"  # пробелы по краям убираются
    for bad in ({"consent": False}, {"name": "   "}, {"name": ""}, {"email": "not-an-email"}, {"comment": "x" * 1001}, {"organization": "o" * 201}):
        with pytest.raises(ValidationError):
            _lead(**bad)


def test_bad_visitor_id_is_dropped_instead_of_failing_the_lead():
    assert _lead(visitor_id="<script>").visitor_id == ""
    assert _lead().visitor_id == VID


def test_events_accept_only_known_names_and_a_sane_visitor_id():
    assert EventIn(visitor_id=VID, name="cta_click").name == "cta_click"
    for bad in ({"name": "hack"}, {"visitor_id": "short"}, {"visitor_id": "x" * 65}, {"visitor_id": "bad id with spaces!"}):
        with pytest.raises(ValidationError):
            EventIn(**{"visitor_id": VID, "name": "cta_click", **bad})


# ---------------- сохранение заявок ----------------

class LeadDb:
    def __init__(self, existing=None, fail_commit=False):
        self.existing, self.fail_commit = existing, fail_commit
        self.added, self.commits, self.rollbacks = [], 0, 0

    async def scalar(self, *_a, **_k):
        return self.existing

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        if self.fail_commit:
            raise IntegrityError("insert", {}, Exception("duplicate"))
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


@pytest.mark.asyncio
async def test_new_lead_is_saved_with_normalized_email_and_a_funnel_event():
    db = LeadDb()
    out = await marketing_service.submit_lead(db, _lead(organization="Юрфирма", role="Юрист / юридическая фирма"))
    assert out.ok and not out.already and out.message == marketing_service.THANKS and db.commits == 1
    lead = next(o for o in db.added if isinstance(o, EarlyAccessRequest))
    assert lead.email == "anna@example.com" and lead.name == "Анна" and lead.organization == "Юрфирма" and lead.consent is True
    event = next(o for o in db.added if isinstance(o, ProductEvent))
    assert event.name == "lead_submit" and event.visitor_id == VID


@pytest.mark.asyncio
async def test_repeated_email_is_not_saved_twice():
    db = LeadDb(existing=uuid.uuid4())
    out = await marketing_service.submit_lead(db, _lead())
    assert out.ok and out.already and db.added == [] and db.commits == 0


@pytest.mark.asyncio
async def test_simultaneous_duplicate_is_reported_as_already_received():
    db = LeadDb(fail_commit=True)
    out = await marketing_service.submit_lead(db, _lead())
    assert out.already and db.rollbacks == 1


@pytest.mark.asyncio
async def test_bot_that_fills_the_hidden_field_gets_a_thank_you_but_nothing_is_stored():
    db = LeadDb()
    out = await marketing_service.submit_lead(db, _lead(website="http://spam.example"))
    assert out.ok and db.added == [] and db.commits == 0


@pytest.mark.asyncio
async def test_lead_without_visitor_id_is_saved_without_an_event():
    db = LeadDb()
    await marketing_service.submit_lead(db, _lead(visitor_id=""))
    assert [type(o) for o in db.added] == [EarlyAccessRequest]


# ---------------- HTTP ----------------

def _client():
    from app.main import create_app

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://test")


@pytest.mark.asyncio
async def test_public_config_reports_whether_registration_is_open(monkeypatch):
    async with _client() as client:
        monkeypatch.setattr(get_settings(), "admin_only_mode", True)
        assert (await client.get("/api/v1/public/config")).json() == {"registration_open": False}
        monkeypatch.setattr(get_settings(), "admin_only_mode", False)
        assert (await client.get("/api/v1/public/config")).json() == {"registration_open": True}


@pytest.mark.asyncio
async def test_invalid_public_requests_are_rejected_before_touching_the_database():
    async with _client() as client:
        no_consent = {"name": "Анна", "email": "a@example.com", "consent": False}
        resp = await client.post("/api/v1/public/early-access", json=no_consent)
        assert resp.status_code == 422 and resp.json()["error"]["code"] == "VALIDATION_ERROR"
        assert resp.json()["error"]["message"] == "Согласие: нужно согласие на обработку данных"  # своё сообщение проверки, не 500
        assert (await client.post("/api/v1/public/early-access", json={"name": "A", "email": "oops", "consent": True})).status_code == 422
        assert (await client.post("/api/v1/public/events", json={"visitor_id": VID, "name": "unknown"})).status_code == 422


@pytest.mark.asyncio
async def test_leads_for_the_admin_are_not_public():
    async with _client() as client:
        assert (await client.get("/api/v1/admin/leads")).status_code == 401


def test_the_hidden_email_list_never_leaks_into_public_schemas():
    from app.main import create_app

    schemas = create_app().openapi()["components"]["schemas"]
    assert "email" not in schemas["EarlyAccessOut"]["properties"] and "email" not in schemas["PublicConfigOut"]["properties"]
