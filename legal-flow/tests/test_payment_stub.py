import json

import pytest

from app.config import get_settings
from app.services.billing_service import sign_fake_webhook
from tests.conftest import unique_email

pytestmark = pytest.mark.asyncio


async def _register_and_order(authed, email_prefix: str):
    email = unique_email(email_prefix)
    await authed.post(
        "/api/v1/auth/register", json={"name": "P", "email": email, "password": "correct-horse-battery"}
    )
    plans = (await authed.get("/api/v1/plans")).json()
    plan = next(p for p in plans if p["code"] == "practice")
    order = await authed.post("/api/v1/orders", json={"plan_code": plan["code"]})
    return order.json()


async def test_pay_button_never_marks_order_paid(authed):
    order = await _register_and_order(authed, "pay-button")
    resp = await authed.post(f"/api/v1/orders/{order['id']}/pay")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "PAYMENT_NOT_CONFIGURED"

    orders = (await authed.get("/api/v1/orders")).json()
    assert orders[0]["status"] == "pending"


async def test_fake_webhook_activates_subscription_and_is_idempotent(authed):
    order = await _register_and_order(authed, "webhook")
    secret = get_settings().payment_webhook_secret

    body = {"provider_event_id": "evt-123", "amount_rub": order["amount_rub"], "currency": "RUB"}
    raw = json.dumps(body, sort_keys=True).encode("utf-8")
    signature = sign_fake_webhook(raw, secret)

    resp1 = await authed.post(
        f"/api/v1/orders/{order['id']}/webhook/fake", json={**body, "signature": signature}
    )
    assert resp1.status_code == 204

    orders = (await authed.get("/api/v1/orders")).json()
    assert orders[0]["status"] == "paid"

    access = (await authed.get("/api/v1/access")).json()
    assert access["plan_code"] == "practice"

    # Replay of the same event id must not error and must not double-apply.
    resp2 = await authed.post(
        f"/api/v1/orders/{order['id']}/webhook/fake", json={**body, "signature": signature}
    )
    assert resp2.status_code == 204


async def test_fake_webhook_rejects_bad_signature(authed):
    order = await _register_and_order(authed, "webhook-badsig")
    body = {"provider_event_id": "evt-bad", "amount_rub": order["amount_rub"], "currency": "RUB"}
    resp = await authed.post(
        f"/api/v1/orders/{order['id']}/webhook/fake", json={**body, "signature": "deadbeef"}
    )
    assert resp.status_code == 422
    orders = (await authed.get("/api/v1/orders")).json()
    assert orders[0]["status"] == "pending"
