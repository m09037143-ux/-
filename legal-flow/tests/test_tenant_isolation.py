import pytest

from tests.conftest import unique_email

pytestmark = pytest.mark.asyncio


async def test_user_cannot_access_another_workspace_via_header(client):
    email_a = unique_email("a")
    resp_a = await client.post(
        "/api/v1/auth/register", json={"name": "A", "email": email_a, "password": "correct-horse-battery"}
    )
    workspace_a_id = resp_a.json()["memberships"][0]["workspace_id"]

    # Second user, separate browser/session (fresh client so cookies don't mix).
    from httpx import AsyncClient, ASGITransport
    from app.main import create_app

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client_b:
        email_b = unique_email("b")
        resp_b = await client_b.post(
            "/api/v1/auth/register", json={"name": "B", "email": email_b, "password": "correct-horse-battery"}
        )
        assert resp_b.status_code == 201
        workspace_b_id = resp_b.json()["memberships"][0]["workspace_id"]
        assert workspace_b_id != workspace_a_id

        # User B tries to read workspace A's access info by spoofing the header.
        spoof = await client_b.get("/api/v1/access", headers={"X-Workspace-Id": workspace_a_id})
        assert spoof.status_code == 403
        assert spoof.json()["error"]["code"] == "FORBIDDEN"

        # User B's own workspace is still reachable.
        own = await client_b.get("/api/v1/access", headers={"X-Workspace-Id": workspace_b_id})
        assert own.status_code == 200


async def test_orders_are_scoped_to_own_workspace(client):
    from httpx import AsyncClient, ASGITransport
    from app.main import create_app

    email_a = unique_email("orders-a")
    resp_a = await client.post(
        "/api/v1/auth/register", json={"name": "A", "email": email_a, "password": "correct-horse-battery"}
    )
    csrf_a = client.cookies.get("pp_csrf")
    plans = (await client.get("/api/v1/plans")).json()
    plan_code = plans[0]["code"]
    order_a = await client.post(
        "/api/v1/orders", json={"plan_code": plan_code}, headers={"X-CSRF-Token": csrf_a}
    )
    assert order_a.status_code == 201

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client_b:
        email_b = unique_email("orders-b")
        await client_b.post(
            "/api/v1/auth/register", json={"name": "B", "email": email_b, "password": "correct-horse-battery"}
        )
        orders_b = await client_b.get("/api/v1/orders")
        assert orders_b.status_code == 200
        assert orders_b.json() == []  # workspace B sees none of workspace A's orders

    orders_a = await client.get("/api/v1/orders", headers={"X-CSRF-Token": csrf_a})
    assert len(orders_a.json()) == 1
