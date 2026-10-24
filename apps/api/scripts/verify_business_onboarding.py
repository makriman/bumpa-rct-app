"""Exercise real HTTP routes and concurrent revisions against disposable Postgres.

Run after migrations with APP_ENV=test and a loopback DATABASE_URL. External
providers are mock adapters; this does not establish live Meta/Hermes behavior.
"""

from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.engine import make_url

from app.core.config import get_settings
from app.db.models import HermesProfile, OtpSession, PhoneIdentity, Tenant, TenantMembership, User
from app.db.session import SessionLocal, set_security_context
from app.main import app


def main() -> None:
    settings = get_settings()
    url = make_url(settings.database_url)
    if (
        settings.app_env != "test"
        or not url.drivername.startswith("postgresql")
        or url.host not in {"127.0.0.1", "localhost"}
        or settings.whatsapp_backend != "mock"
        or settings.agent_backend != "mock"
        or settings.bumpa_backend != "mock"
        or settings.auth_login_mode != "whatsapp_otp"
        or settings.seed_demo_data
    ):
        raise RuntimeError(
            "Use a disposable loopback Postgres test database, mock providers and WhatsApp OTP mode"
        )
    suffix = uuid4().hex
    phone = "+234800" + str(int(suffix[:8], 16) % 10_000_000).zfill(7)
    member_phone = "+234801" + phone[-7:]
    with SessionLocal() as db:
        role = db.execute(
            text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
        ).one()
        if role.rolsuper or role.rolbypassrls:
            raise RuntimeError("The HTTP integration gate requires a non-bypass application role")
        set_security_context(db, privileged=True)
        used_ports = set(db.scalars(select(HermesProfile.api_port)).all())
        profile_port = next(port for port in range(8700, 9000) if port not in used_ports)
        tenant = Tenant(slug=f"onboarding-review-{suffix}", name="Synthetic repair workshop")
        other = Tenant(slug=f"onboarding-other-{suffix}", name="Other synthetic business")
        owner = User(name="Synthetic owner", primary_phone_e164=phone)
        member = User(name="Synthetic member", primary_phone_e164=member_phone)
        db.add_all([tenant, other, owner, member])
        db.flush()
        for user, role in [(owner, "owner"), (member, "member")]:
            db.add(
                TenantMembership(tenant_id=tenant.id, user_id=user.id, role=role, status="active")
            )
            db.add(
                PhoneIdentity(
                    tenant_id=tenant.id,
                    user_id=user.id,
                    phone_e164=user.primary_phone_e164,
                    status="approved",
                )
            )
        db.add(
            HermesProfile(
                tenant_id=tenant.id,
                profile_name=f"review_{suffix}",
                profile_path=f"/profiles/review_{suffix}",
                api_port=profile_port,
                provider="local",
                status="active",
                encrypted_api_key="local-test-only",
            )
        )
        db.commit()
        tenant_id, other_id = tenant.id, other.id
        consent = tenant.research_consent_status

    # Concurrent, simulated first inbound messages must show the same pending
    # question; a greeting is never silently saved as an answer. No Meta sends.
    from app.core.crypto import secret_hash
    from app.services.business_onboarding import arm_pilot_inbound_defaults, chat_reply, state_for

    with SessionLocal() as db:
        set_security_context(db, privileged=True)
        identity = db.scalar(select(PhoneIdentity).where(PhoneIdentity.user_id == owner.id))
        assert identity is not None
        fingerprint = secret_hash("onboarding-existing-pilot:" + phone, settings.otp_secret)
        arm_pilot_inbound_defaults(
            db,
            [
                {
                    "identity_id": identity.id,
                    "tenant_id": tenant_id,
                    "user_id": owner.id,
                    "role": "owner",
                    "phone_fingerprint": fingerprint,
                }
            ],
            settings.otp_secret,
        )
        db.commit()
        identity_id, owner_id = identity.id, owner.id

    def first_inbound(_: int) -> str | None:
        with SessionLocal() as db:
            set_security_context(db, privileged=True)
            business = db.get(Tenant, tenant_id)
            assert business is not None
            result = chat_reply(
                db,
                business,
                owner_id,
                "hi",
                channel="whatsapp",
                whatsapp_identity=(identity_id, fingerprint),
            )
            db.commit()
            return result

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_replies = list(pool.map(first_inbound, range(2)))
    assert all("Question 1 of 7" in str(reply) for reply in first_replies)
    with SessionLocal() as db:
        set_security_context(db, privileged=True)
        business = db.get(Tenant, tenant_id)
        assert business is not None and state_for(db, business)["draft"] == {}
    print(
        "Postgres existing-pilot first-inbound concurrency passed: same question, no greeting saved, no external sends."
    )

    with TestClient(app, raise_server_exceptions=False) as client:

        def login(number: str) -> dict[str, str]:
            requested = client.post("/v1/auth/request-otp", json={"phone_e164": number})
            assert requested.status_code == 202
            verified = client.post(
                "/v1/auth/verify-otp",
                json={
                    "phone_e164": number,
                    "code": requested.json()["dev_code"],
                },
            )
            assert verified.status_code == 200
            return {
                "Authorization": f"Bearer {verified.json()['access_token']}",
                "X-Tenant-ID": tenant_id,
            }

        headers = login(phone)
        member_headers = login(member_phone)
        endpoint = "/v1/settings/business-profile"

        def profile() -> dict:
            response = client.get(endpoint, headers=headers)
            assert response.status_code == 200
            return response.json()

        def ask(message: str, key: str) -> dict:
            response = client.post(
                "/v1/chat/web",
                headers=headers,
                json={
                    "message": message,
                    "client_message_id": f"{suffix}-{key}",
                },
            )
            assert response.status_code == 200, response.text
            return response.json()

        start = ask("business profile", "start")
        revision = profile()["revision"]
        assert (
            ask("business profile", "start")["outbound_message_id"] == start["outbound_message_id"]
        )
        assert profile()["revision"] == revision

        with patch(
            "app.services.chat._record_successful_chat_events",
            side_effect=RuntimeError("test interruption"),
        ):
            interrupted = client.post(
                "/v1/chat/web",
                headers=headers,
                json={
                    "message": "Generator repairs and maintenance",
                    "client_message_id": f"{suffix}-offering",
                },
            )
            assert interrupted.status_code == 500
        assert profile()["step"] == "offering"
        assert profile()["draft"] == {}
        assert "Question 2 of 7" in ask("Generator repairs and maintenance", "offering")["answer"]

        hidden = client.get(endpoint, headers=member_headers)
        assert hidden.status_code == 200 and hidden.json()["draft"] == {}
        assert (
            client.post(
                endpoint,
                headers=member_headers,
                json={
                    "action": "pause",
                    "expected_revision": profile()["revision"],
                },
            ).status_code
            == 403
        )
        assert client.get(endpoint, headers={**headers, "X-Tenant-ID": other_id}).status_code == 403

        stale = profile()["revision"]
        payload = {"action": "pause", "expected_revision": stale}
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(
                pool.map(
                    lambda _: client.post(endpoint, headers=headers, json=payload).status_code,
                    range(2),
                )
            )
        assert sorted(statuses) == [200, 409], statuses
        assert profile()["revision"] == stale + 1
        response = client.post(
            endpoint,
            headers=headers,
            json={
                "action": "resume",
                "expected_revision": profile()["revision"],
            },
        )
        assert response.status_code == 200 and response.json()["step"] == "customers"

        for index, value in enumerate(
            [
                "Households and small shops in Benin City",
                "6, 8: home visits",
                "6",
                "1",
                "8",
                "Calls interrupt my repair visits",
            ]
        ):
            ask(value, f"answer-{index}")
        assert profile()["status"] == "review" and profile()["answers"] == {}
        saved = ask("SAVE", "save")
        final = profile()
        assert final["status"] == "completed" and len(final["answers"]) == 7
        assert final["answers"]["channels"]["detail"] == "home visits"
        assert ask("SAVE", "save")["outbound_message_id"] == saved["outbound_message_id"]
        assert profile()["revision"] == final["revision"]
        member_view = client.get(endpoint, headers=member_headers).json()
        assert member_view["answers"] == final["answers"] and not member_view["can_edit"]

        sends: list[str] = []
        with patch(
            "app.routes.auth.LocalMessagingProvider.send_otp",
            side_effect=lambda destination, _code: sends.append(destination),
        ):
            with ThreadPoolExecutor(max_workers=2) as pool:
                requests = list(
                    pool.map(
                        lambda _: client.post("/v1/auth/request-otp", json={"phone_e164": phone}),
                        range(2),
                    )
                )
        assert [r.status_code for r in requests] == [202, 202]
        assert sends == [phone], sends
        code = next(r.json()["dev_code"] for r in requests if r.json()["dev_code"] is not None)
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(
                pool.map(
                    lambda _: (
                        client.post(
                            "/v1/auth/verify-otp", json={"phone_e164": phone, "code": code}
                        ).status_code
                    ),
                    range(2),
                )
            )
        assert sorted(statuses) == [200, 401], statuses

        # The next challenge's failed attempts must be serialized too.
        requested = client.post("/v1/auth/request-otp", json={"phone_e164": phone})
        wrong_code = "999999" if requested.json()["dev_code"] != "999999" else "000000"
        with ThreadPoolExecutor(max_workers=5) as pool:
            statuses = list(
                pool.map(
                    lambda _: (
                        client.post(
                            "/v1/auth/verify-otp", json={"phone_e164": phone, "code": wrong_code}
                        ).status_code
                    ),
                    range(5),
                )
            )
        assert sorted(statuses) == [401, 401, 401, 401, 423], statuses
        assert (
            client.post(
                "/v1/auth/verify-otp",
                json={"phone_e164": phone, "code": requested.json()["dev_code"]},
            ).status_code
            == 423
        )
        with SessionLocal() as db:
            latest = db.scalar(
                select(OtpSession)
                .where(OtpSession.phone_e164 == phone, OtpSession.purpose == "login")
                .order_by(OtpSession.created_at.desc())
            )
            assert latest is not None and latest.attempts == 5

    with SessionLocal() as db:
        set_security_context(db, tenant_id=tenant_id)
        assert db.get(Tenant, tenant_id).research_consent_status == consent
    print(
        "Postgres HTTP onboarding passed: verified +234 mock login, seven answers, retry atomicity, "
        "duplicate replay, concurrent revisions, member/cross-tenant denials and unchanged consent."
    )
    print(
        "Postgres OTP concurrency passed: one successful consumption, four failed attempts then lockout, no replay."
    )


if __name__ == "__main__":
    main()
