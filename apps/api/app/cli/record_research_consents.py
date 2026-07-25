from __future__ import annotations

import argparse
import re
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import ResearchConsent, Tenant, TenantMembership
from app.db.session import SessionLocal, set_security_context
from app.services.audit import audit

_TENANT_SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,79}")


@dataclass(frozen=True)
class ConsentRecordingResult:
    matched: int
    created: int
    already_current: int


def record_docusign_consents(
    db: Session,
    *,
    tenant_slugs: Sequence[str],
    policy_version: str,
    expected_count: int,
    apply: bool,
) -> ConsentRecordingResult:
    """Record externally signed decisions without storing document content."""

    slugs = tuple(dict.fromkeys(slug.strip().lower() for slug in tenant_slugs))
    if not slugs or len(slugs) != len(tenant_slugs):
        raise ValueError("Tenant slugs must be non-empty and unique")
    if expected_count != len(slugs):
        raise ValueError("Expected count must match the exact tenant target list")
    if not 1 <= expected_count <= 50:
        raise ValueError("Expected count must be between 1 and 50")
    if any(_TENANT_SLUG.fullmatch(slug) is None for slug in slugs):
        raise ValueError("Tenant slug is invalid")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,31}", policy_version):
        raise ValueError("Consent policy version is invalid")

    tenants = list(
        db.scalars(
            select(Tenant)
            .where(Tenant.slug.in_(slugs), Tenant.status == "active")
            .order_by(Tenant.slug)
        ).all()
    )
    if len(tenants) != expected_count:
        raise ValueError("Exact active tenant target set was not found")

    created = 0
    already_current = 0
    for tenant in tenants:
        owner_ids = list(
            db.scalars(
                select(TenantMembership.user_id)
                .where(
                    TenantMembership.tenant_id == tenant.id,
                    TenantMembership.role == "owner",
                    TenantMembership.status == "active",
                )
                .order_by(TenantMembership.user_id)
            ).all()
        )
        if len(owner_ids) != 1:
            raise ValueError("Each target tenant must have exactly one active owner")
        owner_id = owner_ids[0]
        current_history = db.scalar(
            select(ResearchConsent.id)
            .where(
                ResearchConsent.tenant_id == tenant.id,
                ResearchConsent.status == "granted",
                ResearchConsent.policy_version == policy_version,
                ResearchConsent.actor_user_id == owner_id,
            )
            .order_by(ResearchConsent.recorded_at.desc(), ResearchConsent.id.desc())
            .limit(1)
        )
        if tenant.research_consent_status == "granted" and current_history is not None:
            already_current += 1
            continue
        created += 1
        if not apply:
            continue
        before = tenant.research_consent_status
        tenant.research_consent_status = "granted"
        db.add(
            ResearchConsent(
                tenant_id=tenant.id,
                status="granted",
                policy_version=policy_version,
                actor_user_id=owner_id,
            )
        )
        audit(
            db,
            actor_user_id=owner_id,
            tenant_id=tenant.id,
            action="research.consent.changed",
            resource_type="tenant",
            resource_id=tenant.id,
            before={"status": before},
            after={
                "status": "granted",
                "policy_version": policy_version,
                "attestation_source": "docusign",
                "recording_mode": "operator_from_signed_document",
            },
        )

    return ConsentRecordingResult(
        matched=len(tenants),
        created=created,
        already_current=already_current,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Record externally signed research-consent decisions for an exact tenant list. "
            "Document content and participant identities are not accepted."
        )
    )
    parser.add_argument("--tenant-slug", action="append", required=True)
    parser.add_argument("--expected-count", type=int, required=True)
    parser.add_argument("--policy-version")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Commit the changes. Without this flag the command performs a dry run.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    settings = get_settings()
    policy_version = args.policy_version or settings.research_consent_policy_version
    with SessionLocal() as db:
        set_security_context(db, privileged=True)
        try:
            result = record_docusign_consents(
                db,
                tenant_slugs=args.tenant_slug,
                policy_version=policy_version,
                expected_count=args.expected_count,
                apply=args.apply,
            )
        except ValueError as exc:
            db.rollback()
            raise SystemExit(str(exc)) from exc
        if args.apply:
            db.commit()
        else:
            db.rollback()
    mode = "applied" if args.apply else "dry-run"
    print(
        f"{mode}: matched={result.matched} created={result.created} "
        f"already_current={result.already_current}"
    )


if __name__ == "__main__":
    main()
