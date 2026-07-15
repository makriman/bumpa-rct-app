from __future__ import annotations

import json
from datetime import UTC, date, datetime
from io import BytesIO, StringIO
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.cli import onboard as cli
from app.core.crypto import FieldCipher
from app.db.base import Base
from app.db.models import (
    AuditLog,
    BumpaConnection,
    BumpaOrder,
    BumpaSyncRun,
    PhoneIdentity,
    PlatformRole,
    Tenant,
    TenantMembership,
    User,
)

FIELD_KEY = "test-field-encryption-key-that-is-not-production"
API_KEY = "synthetic-bumpa-api-key-never-print"
OWNER_PHONE = "+2348000000101"
OPERATOR_PHONE = "+2348000000102"


class VerifiedBumpaClient:
    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    def __enter__(self) -> VerifiedBumpaClient:
        return self

    def __exit__(self, *_args: object) -> None:
        pass

    def verify(self) -> None:
        pass


@pytest.fixture(autouse=True)
def verified_bumpa(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "BumpaClient", VerifiedBumpaClient)


@pytest.fixture
def session_factory() -> sessionmaker[Session]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _payload(*, apply: bool = False, bootstrap: bool = True) -> dict[str, object]:
    payload: dict[str, object] = {
        "tenant": {"slug": "synthetic-shop", "name": "Synthetic Shop"},
        "owner": {"name": "Synthetic Owner", "phone_e164": OWNER_PHONE},
        "operator": {
            "name": "Synthetic Operator",
            "phone_e164": OPERATOR_PHONE,
            "bootstrap_if_missing": bootstrap,
        },
        "bumpa": {
            "api_key": API_KEY,
            "business_id": "synthetic-business-101",
            "store_timezone": "Africa/Lagos",
            "store_currency": "NGN",
        },
    }
    if apply:
        payload["apply"] = True
        payload["confirmation"] = "APPLY synthetic-shop"
    return payload


def _bundle(*, apply: bool = False, bootstrap: bool = True) -> cli.OnboardingBundle:
    return cli.OnboardingBundle.model_validate(_payload(apply=apply, bootstrap=bootstrap))


def test_bundle_is_strict_normalized_and_apply_requires_tenant_confirmation() -> None:
    invalid = _payload()
    invalid["tenant"] = {"slug": "Not-Normalized", "name": " Synthetic Shop"}
    invalid["unexpected"] = "rejected"
    with pytest.raises(ValueError):
        cli.OnboardingBundle.model_validate(invalid)

    missing_confirmation = _payload()
    missing_confirmation["apply"] = True
    with pytest.raises(ValueError, match="confirmation"):
        cli.OnboardingBundle.model_validate(missing_confirmation)

    wrong_confirmation = _payload(apply=True)
    wrong_confirmation["confirmation"] = "APPLY another-shop"
    with pytest.raises(ValueError, match="confirmation"):
        cli.OnboardingBundle.model_validate(wrong_confirmation)

    with_confirmation_in_dry_run = _payload()
    with_confirmation_in_dry_run["confirmation"] = "APPLY synthetic-shop"
    with pytest.raises(ValueError, match="Dry-run"):
        cli.OnboardingBundle.model_validate(with_confirmation_in_dry_run)


def test_stdin_parser_rejects_duplicates_oversize_and_non_object_without_echoing_values() -> None:
    with pytest.raises(cli.OnboardingError) as duplicate:
        cli.parse_stdin_bundle(BytesIO(b'{"tenant":{},"tenant":{}}'))
    assert duplicate.value.code == "duplicate_json_key"

    with pytest.raises(cli.OnboardingError) as oversized:
        cli.parse_stdin_bundle(BytesIO(b"x" * (cli.MAX_STDIN_BYTES + 1)))
    assert oversized.value.code == "input_too_large"

    with pytest.raises(cli.OnboardingError) as non_object:
        cli.parse_stdin_bundle(BytesIO(b"[]"))
    assert non_object.value.code == "input_must_be_object"

    invalid = _payload()
    invalid["owner"] = {"name": "Synthetic Owner", "phone_e164": "not-a-phone"}
    with pytest.raises(cli.OnboardingError) as validation:
        cli.parse_stdin_bundle(BytesIO(json.dumps(invalid).encode()))
    assert validation.value.code == "validation_failed"
    assert validation.value.fields == ["owner.phone_e164"]
    assert "not-a-phone" not in json.dumps(cli._safe_error(validation.value))


def test_dry_run_is_default_and_rolls_back_every_planned_change(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as db:
        result = cli.onboard(db, _bundle(), field_encryption_key=FIELD_KEY)
        assert result.ids == {}
        assert result.counts["dry_run"] == 1
        assert result.counts["applied"] == 0
        assert result.counts["created"] == 7

    with session_factory() as db:
        for model in (
            Tenant,
            User,
            PlatformRole,
            TenantMembership,
            PhoneIdentity,
            BumpaConnection,
            AuditLog,
        ):
            assert db.scalar(select(func.count()).select_from(model)) == 0

    with session_factory() as db:
        with pytest.raises(cli.OnboardingError) as weak_key:
            cli.onboard(db, _bundle(), field_encryption_key="too-short")
        assert weak_key.value.code == "field_encryption_key_invalid"


def test_apply_atomically_creates_encrypted_upserts_and_redacted_audits(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as db:
        result = cli.onboard(db, _bundle(apply=True), field_encryption_key=FIELD_KEY)
        public = json.dumps(result.public_dict(), sort_keys=True)
        assert result.counts == {
            "created": 7,
            "updated": 0,
            "unchanged": 0,
            "audit_rows": 7,
            "applied": 1,
            "dry_run": 0,
        }
        assert set(result.ids) == {
            "operator_user_id",
            "tenant_id",
            "owner_user_id",
            "membership_id",
            "phone_identity_id",
            "bumpa_connection_id",
        }
        assert API_KEY not in public
        assert OWNER_PHONE not in public
        assert OPERATOR_PHONE not in public

    with session_factory() as db:
        connection = db.scalar(select(BumpaConnection))
        assert connection is not None
        assert connection.provider == "bumpa"
        assert connection.scope_type == "business_id"
        assert connection.store_timezone == "Africa/Lagos"
        assert connection.store_currency == "NGN"
        assert connection.encrypted_api_key != API_KEY
        assert FieldCipher(FIELD_KEY).decrypt(connection.encrypted_api_key) == API_KEY
        assert db.scalar(select(func.count()).select_from(Tenant)) == 1
        assert db.scalar(select(func.count()).select_from(User)) == 2
        assert db.scalar(select(func.count()).select_from(PlatformRole)) == 1
        assert db.scalar(select(func.count()).select_from(TenantMembership)) == 1
        assert db.scalar(select(func.count()).select_from(PhoneIdentity)) == 1
        audits = list(db.scalars(select(AuditLog)).all())
        assert len(audits) == 7
        serialized_audits = json.dumps(
            [
                {
                    "action": row.action,
                    "before": row.before,
                    "after": row.after,
                }
                for row in audits
            ],
            sort_keys=True,
        )
        assert API_KEY not in serialized_audits
        assert OWNER_PHONE not in serialized_audits
        assert OPERATOR_PHONE not in serialized_audits


def test_apply_is_idempotent_and_requires_explicit_bootstrap_authority(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as db:
        with pytest.raises(cli.OnboardingError) as missing_operator:
            cli.onboard(db, _bundle(apply=True, bootstrap=False), field_encryption_key=FIELD_KEY)
        assert missing_operator.value.code == "operator_not_found"
    with session_factory() as db:
        assert db.scalar(select(func.count()).select_from(User)) == 0

    with session_factory() as db:
        first = cli.onboard(db, _bundle(apply=True), field_encryption_key=FIELD_KEY)
        assert first.counts["applied"] == 1
    with session_factory() as db:
        second = cli.onboard(db, _bundle(apply=True), field_encryption_key=FIELD_KEY)
        assert second.counts["created"] == 0
        assert second.counts["updated"] == 0
        assert second.counts["unchanged"] == 6
        assert second.counts["audit_rows"] == 1
    with session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Tenant)) == 1
        assert db.scalar(select(func.count()).select_from(User)) == 2
        assert db.scalar(select(func.count()).select_from(BumpaConnection)) == 1


def test_connection_upsert_distinguishes_key_rotation_from_material_boundary_change(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as db:
        cli.onboard(db, _bundle(apply=True), field_encryption_key=FIELD_KEY)
        connection = db.scalar(select(BumpaConnection))
        tenant = db.scalar(select(Tenant))
        assert connection is not None and tenant is not None
        connection.last_successful_sync_at = datetime(2026, 7, 15, 10, tzinfo=UTC)
        db.add_all(
            [
                BumpaSyncRun(
                    id="cli-boundary-run",
                    tenant_id=tenant.id,
                    bumpa_connection_id=connection.id,
                    boundary_revision=connection.boundary_revision,
                    status="success",
                    completion_quality="complete",
                    requested_from=date(2026, 7, 1),
                    requested_to=date(2026, 7, 14),
                    started_at=datetime(2026, 7, 15, 9, tzinfo=UTC),
                    finished_at=datetime(2026, 7, 15, 10, tzinfo=UTC),
                    orders_availability="available",
                    orders_count=1,
                ),
                BumpaOrder(
                    tenant_id=tenant.id,
                    bumpa_order_id="cli-boundary-order",
                    raw_payload={"id": "cli-boundary-order"},
                ),
            ]
        )
        db.commit()

    rotated_payload = _payload(apply=True)
    rotated_bumpa = rotated_payload["bumpa"]
    assert isinstance(rotated_bumpa, dict)
    rotated_payload["bumpa"] = {
        **rotated_bumpa,
        "api_key": "rotated-synthetic-key",
    }
    with session_factory() as db:
        result = cli.onboard(
            db,
            cli.OnboardingBundle.model_validate(rotated_payload),
            field_encryption_key=FIELD_KEY,
        )
        connection = db.scalar(select(BumpaConnection))
        assert connection is not None
        assert result.counts["updated"] == 1
        assert connection.boundary_revision == 1
        assert connection.last_successful_sync_at is not None
        assert db.scalar(select(func.count()).select_from(BumpaOrder)) == 1

    changed_payload = _payload(apply=True)
    changed_bumpa = changed_payload["bumpa"]
    assert isinstance(changed_bumpa, dict)
    changed_payload["bumpa"] = {
        **changed_bumpa,
        "api_key": "new-store-synthetic-key",
        "business_id": "synthetic-business-202",
    }
    with session_factory() as db:
        cli.onboard(
            db,
            cli.OnboardingBundle.model_validate(changed_payload),
            field_encryption_key=FIELD_KEY,
        )
        connection = db.scalar(select(BumpaConnection))
        assert connection is not None
        assert connection.scope_id == "synthetic-business-202"
        assert connection.boundary_revision == 2
        assert connection.last_successful_sync_at is None
        assert db.scalar(select(func.count()).select_from(BumpaOrder)) == 0
        assert db.scalar(select(func.count()).select_from(BumpaSyncRun)) == 1


@pytest.mark.parametrize(
    ("record", "field", "value", "error_code"),
    [
        (Tenant, "status", "suspended", "tenant_inactive"),
        (User, "status", "inactive", "owner_inactive"),
        (
            TenantMembership,
            "status",
            "revoked",
            "owner_membership_inactive_or_conflicting",
        ),
        (PhoneIdentity, "opt_out", True, "phone_identity_opted_out"),
        (PhoneIdentity, "status", "pending", "phone_identity_not_approved"),
        (BumpaConnection, "status", "inactive", "bumpa_connection_inactive"),
    ],
)
def test_rerun_fails_closed_without_reactivating_existing_state(
    session_factory: sessionmaker[Session],
    record: type[Tenant | User | TenantMembership | PhoneIdentity | BumpaConnection],
    field: str,
    value: object,
    error_code: str,
) -> None:
    with session_factory() as db:
        cli.onboard(db, _bundle(apply=True), field_encryption_key=FIELD_KEY)
    with session_factory() as db:
        if record is User:
            row = db.scalar(select(User).where(User.primary_phone_e164 == OWNER_PHONE))
        else:
            row = db.scalar(select(record))
        assert row is not None
        setattr(row, field, value)
        db.commit()

    with session_factory() as db:
        with pytest.raises(cli.OnboardingError) as rejected:
            cli.onboard(db, _bundle(apply=True), field_encryption_key=FIELD_KEY)
        assert rejected.value.code == error_code

    with session_factory() as db:
        if record is User:
            preserved = db.scalar(select(User).where(User.primary_phone_e164 == OWNER_PHONE))
        else:
            preserved = db.scalar(select(record))
        assert preserved is not None
        assert getattr(preserved, field) == value


def test_dual_role_superadmin_owner_preserves_shared_user_name(
    session_factory: sessionmaker[Session],
) -> None:
    payload = _payload(apply=True)
    payload["owner"] = {
        "name": "Stable Shared Name",
        "phone_e164": OPERATOR_PHONE,
    }
    payload["operator"] = {
        "name": "Stable Shared Name",
        "phone_e164": OPERATOR_PHONE,
        "bootstrap_if_missing": True,
        "platform_role": "superadmin",
    }
    bundle = cli.OnboardingBundle.model_validate(payload)

    with session_factory() as db:
        result = cli.onboard(db, bundle, field_encryption_key=FIELD_KEY)
        assert result.counts["applied"] == 1
        assert result.ids["operator_user_id"] == result.ids["owner_user_id"]

    with session_factory() as db:
        user = db.scalar(select(User).where(User.primary_phone_e164 == OPERATOR_PHONE))
        assert user is not None and user.name == "Stable Shared Name"
        assert (
            db.scalar(
                select(PlatformRole).where(
                    PlatformRole.user_id == user.id,
                    PlatformRole.role == "superadmin",
                )
            )
            is not None
        )
        membership = db.scalar(select(TenantMembership).where(TenantMembership.user_id == user.id))
        assert membership is not None and membership.role == "owner"


@pytest.mark.parametrize("apply", [False, True])
def test_provider_verification_failure_rolls_back_without_exposing_credentials(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch, apply: bool
) -> None:
    class RejectedBumpaClient(VerifiedBumpaClient):
        def verify(self) -> None:
            raise cli.BumpaProviderError("sanitized provider rejection", status_code=403)

    monkeypatch.setattr(cli, "BumpaClient", RejectedBumpaClient)
    with session_factory() as db:
        with pytest.raises(cli.OnboardingError) as rejected:
            cli.onboard(db, _bundle(apply=apply), field_encryption_key=FIELD_KEY)
        assert rejected.value.code == "bumpa_connection_verification_failed"
        assert API_KEY not in str(rejected.value)

    with session_factory() as db:
        for model in (Tenant, User, PlatformRole, BumpaConnection, AuditLog):
            assert db.scalar(select(func.count()).select_from(model)) == 0


def test_late_identity_conflict_rolls_back_the_entire_onboarding_transaction(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as db:
        operator = User(
            name="Existing Operator",
            primary_phone_e164=OPERATOR_PHONE,
            status="active",
        )
        existing_tenant = Tenant(slug="existing-shop", name="Existing Shop")
        existing_owner = User(
            name="Existing Owner",
            primary_phone_e164=OWNER_PHONE,
            status="active",
        )
        db.add_all((operator, existing_tenant, existing_owner))
        db.flush()
        db.add_all(
            (
                PlatformRole(user_id=operator.id, role="operator"),
                TenantMembership(
                    tenant_id=existing_tenant.id,
                    user_id=existing_owner.id,
                    role="owner",
                    status="active",
                ),
                PhoneIdentity(
                    tenant_id=existing_tenant.id,
                    user_id=existing_owner.id,
                    phone_e164=OWNER_PHONE,
                    status="approved",
                ),
            )
        )
        db.commit()

    with session_factory() as db:
        with pytest.raises(cli.OnboardingError) as conflict:
            cli.onboard(db, _bundle(apply=True, bootstrap=False), field_encryption_key=FIELD_KEY)
        assert conflict.value.code == "phone_identity_conflict"

    with session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Tenant)) == 1
        assert db.scalar(select(func.count()).select_from(TenantMembership)) == 1
        assert db.scalar(select(func.count()).select_from(BumpaConnection)) == 0
        owner = db.scalar(select(User).where(User.primary_phone_e164 == OWNER_PHONE))
        assert owner is not None and owner.name == "Existing Owner"
        assert db.scalar(select(func.count()).select_from(AuditLog)) == 0


def test_cli_reads_bundle_from_stdin_and_emits_only_ids_and_counts(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "SessionLocal", session_factory)
    monkeypatch.setattr(
        cli,
        "get_settings",
        lambda: SimpleNamespace(field_encryption_key=FIELD_KEY),
    )
    stdout = StringIO()
    stderr = StringIO()
    exit_code = cli.main(
        stdin=BytesIO(json.dumps(_payload(apply=True)).encode()),
        stdout=stdout,
        stderr=stderr,
    )
    assert exit_code == 0
    assert stderr.getvalue() == ""
    output = json.loads(stdout.getvalue())
    assert set(output) == {"ids", "counts"}
    rendered = stdout.getvalue()
    assert API_KEY not in rendered
    assert OWNER_PHONE not in rendered
    assert OPERATOR_PHONE not in rendered

    invalid = _payload()
    invalid["owner"] = {"name": "Synthetic Owner", "phone_e164": OWNER_PHONE + "invalid"}
    failed_stdout = StringIO()
    failed_stderr = StringIO()
    failed_code = cli.main(
        stdin=BytesIO(json.dumps(invalid).encode()),
        stdout=failed_stdout,
        stderr=failed_stderr,
    )
    assert failed_code == 2
    assert failed_stdout.getvalue() == ""
    assert OWNER_PHONE not in failed_stderr.getvalue()
    assert API_KEY not in failed_stderr.getvalue()
