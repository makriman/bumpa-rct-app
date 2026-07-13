from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.request_context import build_audit_request_context
from app.db.models import AuditLog
from app.db.session import SessionLocal, set_security_context
from app.main import app
from tests.conftest import auth_headers


def test_request_context_anonymizes_addresses_and_redacts_untrusted_user_agents() -> None:
    phone = "+234 800 123 4567"
    email = "private-owner@example.test"
    credential = "this-is-a-secret-bearer-value"
    context = build_audit_request_context(
        client_host="2001:db8:abcd:1234:5678:90ab:cdef:0123",
        user_agent=(
            "Mozilla/5.0\x00 (Linux; Android 14) AppleWebKit/537.36 Chrome/126.0 "
            f"contact={email} phone={phone} Bearer {credential} https://private.test/path"
        ),
    )

    assert context.client_ip == "2001:db8:abcd::"
    assert context.user_agent == "client=chrome/126; platform=android"
    assert phone not in context.user_agent
    assert email not in context.user_agent
    assert credential not in context.user_agent

    unknown = build_audit_request_context(
        client_host="not-an-address",
        user_agent="John Doe, 14 Private Street, private@example.test, secret-value",
    )
    assert unknown.client_ip is None
    assert unknown.user_agent == "client=other; platform=other"
    assert "John" not in unknown.user_agent

    mapped = build_audit_request_context(client_host="::ffff:203.0.113.87", user_agent=None)
    assert mapped.client_ip == "203.0.113.0"
    assert mapped.user_agent is None


def test_generic_audit_uses_privacy_bounded_asgi_request_context() -> None:
    correlation_id = str(uuid4())
    phone = "+234 800 777 8888"
    email = "ua-private@example.test"
    secret = "bearer-secret-must-not-persist"
    user_agent = (
        "Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 Chrome/127.0 "
        f"contact={email} phone={phone} Bearer {secret}"
    )

    with TestClient(app, client=("203.0.113.87", 50000)) as scoped_client:
        owner = auth_headers(scoped_client, "+2348012345678")
        profile = scoped_client.get("/v1/settings/profile", headers=owner)
        assert profile.status_code == 200
        response = scoped_client.patch(
            "/v1/settings/profile",
            headers={
                **owner,
                "X-Correlation-ID": correlation_id,
                # Application code must use the ASGI peer selected by the edge
                # trust boundary, never parse a caller-controlled forwarding chain.
                "X-Forwarded-For": "198.51.100.99",
                "User-Agent": user_agent,
            },
            json={"name": profile.json()["name"]},
        )
        assert response.status_code == 200

    with SessionLocal() as db:
        set_security_context(db, privileged=True)
        record = db.scalar(
            select(AuditLog).where(
                AuditLog.action == "user.profile.updated",
                AuditLog.correlation_id == correlation_id,
            )
        )
        assert record is not None
        assert record.ip_address == "203.0.113.0"
        assert record.user_agent == "client=chrome/127; platform=windows"
        assert phone not in record.user_agent
        assert email not in record.user_agent
        assert secret not in record.user_agent
