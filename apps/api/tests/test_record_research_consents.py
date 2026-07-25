from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.cli.record_research_consents import main, record_docusign_consents
from app.core.ids import new_id
from app.db.models import AuditLog, ResearchConsent, Tenant, TenantMembership, User
from app.db.session import SessionLocal, set_security_context


def _pilot_tenant(db: Session, *, slug: str, phone: str) -> tuple[Tenant, User]:
    tenant = Tenant(
        id=new_id(),
        slug=slug,
        name="Consent Test Business",
        status="active",
        timezone="Africa/Lagos",
        currency_code="NGN",
        research_consent_status="pending",
    )
    user = User(id=new_id(), name="Consent Test Owner", primary_phone_e164=phone, status="active")
    membership = TenantMembership(
        id=new_id(),
        tenant_id=tenant.id,
        user_id=user.id,
        role="owner",
        status="active",
    )
    db.add_all([tenant, user, membership])
    db.flush()
    return tenant, user


def test_records_docusign_consent_with_bounded_audit_metadata(client: TestClient) -> None:
    with SessionLocal() as db:
        set_security_context(db, privileged=True)
        tenant, owner = _pilot_tenant(
            db,
            slug="consent-cli-one",
            phone="+2347000001101",
        )
        result = record_docusign_consents(
            db,
            tenant_slugs=[tenant.slug],
            policy_version="pilot-v2",
            expected_count=1,
            apply=True,
        )
        db.commit()

        assert result.created == 1
        assert tenant.research_consent_status == "granted"
        consent = db.scalar(select(ResearchConsent).where(ResearchConsent.tenant_id == tenant.id))
        assert consent is not None
        assert consent.actor_user_id == owner.id
        assert consent.policy_version == "pilot-v2"
        audit = db.scalar(
            select(AuditLog).where(
                AuditLog.tenant_id == tenant.id,
                AuditLog.action == "research.consent.changed",
            )
        )
        assert audit is not None
        assert audit.after == {
            "status": "granted",
            "policy_version": "pilot-v2",
            "attestation_source": "docusign",
            "recording_mode": "operator_from_signed_document",
        }


def test_recording_is_idempotent_and_dry_run_does_not_mutate(client: TestClient) -> None:
    with SessionLocal() as db:
        set_security_context(db, privileged=True)
        tenant, _owner = _pilot_tenant(
            db,
            slug="consent-cli-two",
            phone="+2347000001102",
        )
        preview = record_docusign_consents(
            db,
            tenant_slugs=[tenant.slug],
            policy_version="pilot-v2",
            expected_count=1,
            apply=False,
        )
        assert preview.created == 1
        assert tenant.research_consent_status == "pending"

        first = record_docusign_consents(
            db,
            tenant_slugs=[tenant.slug],
            policy_version="pilot-v2",
            expected_count=1,
            apply=True,
        )
        db.commit()
        second = record_docusign_consents(
            db,
            tenant_slugs=[tenant.slug],
            policy_version="pilot-v2",
            expected_count=1,
            apply=True,
        )
        db.commit()
        assert first.created == 1
        assert second.created == 0
        assert second.already_current == 1
        assert (
            len(
                list(
                    db.scalars(
                        select(ResearchConsent).where(ResearchConsent.tenant_id == tenant.id)
                    ).all()
                )
            )
            == 1
        )


def test_recording_validation_fails_closed(client: TestClient) -> None:
    with SessionLocal() as db:
        set_security_context(db, privileged=True)
        tenant, _owner = _pilot_tenant(
            db,
            slug="consent-cli-validation",
            phone="+2347000001103",
        )
        with pytest.raises(ValueError, match="non-empty and unique"):
            record_docusign_consents(
                db,
                tenant_slugs=[],
                policy_version="pilot-v2",
                expected_count=0,
                apply=False,
            )
        with pytest.raises(ValueError, match="non-empty and unique"):
            record_docusign_consents(
                db,
                tenant_slugs=[tenant.slug, tenant.slug],
                policy_version="pilot-v2",
                expected_count=2,
                apply=False,
            )
        with pytest.raises(ValueError, match="Expected count"):
            record_docusign_consents(
                db,
                tenant_slugs=[tenant.slug],
                policy_version="pilot-v2",
                expected_count=2,
                apply=False,
            )
        with pytest.raises(ValueError, match="between 1 and 50"):
            record_docusign_consents(
                db,
                tenant_slugs=[f"valid-slug-{index}" for index in range(51)],
                policy_version="pilot-v2",
                expected_count=51,
                apply=False,
            )
        with pytest.raises(ValueError, match="slug is invalid"):
            record_docusign_consents(
                db,
                tenant_slugs=["invalid_slug"],
                policy_version="pilot-v2",
                expected_count=1,
                apply=False,
            )
        with pytest.raises(ValueError, match="policy version"):
            record_docusign_consents(
                db,
                tenant_slugs=[tenant.slug],
                policy_version="invalid version",
                expected_count=1,
                apply=False,
            )
        with pytest.raises(ValueError, match="target set"):
            record_docusign_consents(
                db,
                tenant_slugs=["missing-consent-tenant"],
                policy_version="pilot-v2",
                expected_count=1,
                apply=False,
            )


def test_cli_dry_run_apply_and_sanitized_failure(
    client: TestClient,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with SessionLocal() as db:
        set_security_context(db, privileged=True)
        tenant, _owner = _pilot_tenant(
            db,
            slug="consent-cli-main",
            phone="+2347000001104",
        )
        db.commit()

    main(
        [
            "--tenant-slug",
            tenant.slug,
            "--expected-count",
            "1",
            "--policy-version",
            "pilot-v2",
        ]
    )
    assert capsys.readouterr().out == "dry-run: matched=1 created=1 already_current=0\n"

    main(
        [
            "--tenant-slug",
            tenant.slug,
            "--expected-count",
            "1",
            "--policy-version",
            "pilot-v2",
            "--apply",
        ]
    )
    assert capsys.readouterr().out == "applied: matched=1 created=1 already_current=0\n"

    with pytest.raises(SystemExit, match="Exact active tenant target set"):
        main(
            [
                "--tenant-slug",
                "missing-cli-consent-tenant",
                "--expected-count",
                "1",
            ]
        )
