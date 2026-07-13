#!/usr/bin/env python3
"""Secure Bumpa Bestie production onboarding and provider canaries.

The helper deliberately keeps credentials in memory and streams them over SSH
stdin. It never puts a phone number, provider key, or access token in argv,
stdout, a temporary file, or an environment variable.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import shlex
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

DEFAULT_SECRET_FILE = Path.cwd() / "bumpa bestie secrets.md"
DEFAULT_HOST = "root@165.227.228.20"
DEFAULT_API_BASE = "https://api.bumpabestie.com/v1"
REMOTE_ROOT = "/opt/bumpabestie"
OPS_USER_AGENT = "BumpaBestieProductionOps/1.0"
E164_RE = re.compile(r"^\+[1-9]\d{7,14}$")
BUSINESS_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$")
UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
SSH_HOST_RE = re.compile(r"^(?:[A-Za-z0-9_.-]+@)?[A-Za-z0-9.-]+$")
EXPECTED_ID_KEYS = {
    "operator_user_id",
    "tenant_id",
    "owner_user_id",
    "membership_id",
    "phone_identity_id",
    "bumpa_connection_id",
}
EXPECTED_COUNT_KEYS = {
    "created",
    "updated",
    "unchanged",
    "audit_rows",
    "applied",
    "dry_run",
}
EXPECTED_SYNC_DATASETS = frozenset(
    {
        "sales.overview",
        "sales.total_sales",
        "sales.gross_profit",
        "sales.net_profit",
        "products.overview",
        "products.products_sold",
        "products.top_selling_products",
        "products.least_selling_products",
        "customers.overview",
        "customers.top_customers_order",
    }
)
OPTIONAL_UNAVAILABLE_SYNC_DATASETS = frozenset(
    {"sales.gross_profit", "sales.net_profit"}
)


class OpsError(RuntimeError):
    pass


@dataclass(frozen=True)
class Store:
    index: int
    api_key: str
    business_id: str
    owner_phone: str

    @property
    def slug(self) -> str:
        stable_id = hashlib.sha256(self.business_id.encode("utf-8")).hexdigest()[:12]
        return f"bumpa-business-{stable_id}"

    @property
    def name(self) -> str:
        stable_id = (
            hashlib.sha256(self.business_id.encode("utf-8")).hexdigest()[:8].upper()
        )
        return f"Bumpa Business {stable_id}"


@dataclass(frozen=True)
class Inputs:
    operator_phone: str
    stores: tuple[Store, ...]


ISSUE_SESSION_PROGRAM = r"""
import json
import sys
from datetime import timedelta
from sqlalchemy import select
from app.core.config import get_settings
from app.core.security import create_access_token
from app.core.time import utcnow
from app.db.models import PlatformRole, TenantMembership, User
from app.db.session import SessionLocal, set_security_context

request = json.load(sys.stdin)
with SessionLocal() as db:
    set_security_context(db, privileged=True)
    user = db.scalar(select(User).where(
        User.primary_phone_e164 == request["phone_e164"],
        User.status == "active",
    ))
    if user is None:
        raise RuntimeError("approved_user_not_found")
    if request["kind"] == "operator":
        role = db.scalar(select(PlatformRole).where(
            PlatformRole.user_id == user.id,
            PlatformRole.role.in_(("operator", "superadmin")),
        ))
        if role is None:
            raise RuntimeError("operator_role_required")
    elif request["kind"] == "owner":
        membership = db.scalar(select(TenantMembership).where(
            TenantMembership.user_id == user.id,
            TenantMembership.tenant_id == request["tenant_id"],
            TenantMembership.role.in_(("owner", "admin")),
            TenantMembership.status == "active",
        ))
        if membership is None:
            raise RuntimeError("tenant_admin_membership_required")
    else:
        raise RuntimeError("invalid_session_kind")
    token, session = create_access_token(db, user, get_settings())
    session.expires_at = utcnow() + timedelta(minutes=5)
    db.commit()
    print(json.dumps({"token": token, "session_id": session.id}, sort_keys=True))
""".strip()


REVOKE_SESSION_PROGRAM = r"""
import sys
from app.core.config import get_settings
from app.core.security import revoke_token
from app.db.session import SessionLocal, set_security_context

token = sys.stdin.read(16384).strip()
if not token:
    raise RuntimeError("empty_token")
with SessionLocal() as db:
    set_security_context(db, privileged=True)
    revoke_token(db, token, get_settings())
print("ok")
""".strip()


AUDIT_ONBOARDING_PROGRAM = r"""
import hmac
import json
import sys
from sqlalchemy import select
from app.core.config import get_settings
from app.core.crypto import FieldCipher
from app.db.models import (
    AuditLog,
    BumpaConnection,
    PhoneIdentity,
    PlatformRole,
    Tenant,
    TenantMembership,
    User,
)
from app.db.session import SessionLocal, set_security_context

expected = json.load(sys.stdin)
stores = expected.get("stores")
if not isinstance(stores, list) or len(stores) != 5:
    raise RuntimeError("invalid_expected_store_count")

with SessionLocal() as db:
    set_security_context(db, privileged=True)
    cipher = FieldCipher.from_settings(get_settings())
    operator = db.scalar(select(User).where(
        User.primary_phone_e164 == expected.get("operator_phone"),
        User.status == "active",
    ))
    if operator is None:
        raise RuntimeError("operator_missing")
    superadmin = db.scalar(select(PlatformRole).where(
        PlatformRole.user_id == operator.id,
        PlatformRole.role == "superadmin",
    ))
    if superadmin is None:
        raise RuntimeError("superadmin_role_missing")

    tenant_ids = set()
    owner_ids = set()
    dual_role_count = 0
    for store in stores:
        tenant = db.scalar(select(Tenant).where(Tenant.slug == store.get("slug")))
        if tenant is None or tenant.status != "active":
            raise RuntimeError("tenant_invariant_failed")
        owner = db.scalar(select(User).where(
            User.primary_phone_e164 == store.get("owner_phone"),
            User.status == "active",
        ))
        if owner is None:
            raise RuntimeError("owner_invariant_failed")
        membership = db.scalar(select(TenantMembership).where(
            TenantMembership.tenant_id == tenant.id,
            TenantMembership.user_id == owner.id,
            TenantMembership.role == "owner",
            TenantMembership.status == "active",
        ))
        if membership is None:
            raise RuntimeError("owner_membership_invariant_failed")
        active_owners = list(db.scalars(select(TenantMembership).where(
            TenantMembership.tenant_id == tenant.id,
            TenantMembership.role == "owner",
            TenantMembership.status == "active",
        )).all())
        if len(active_owners) != 1 or active_owners[0].user_id != owner.id:
            raise RuntimeError("unexpected_owner_mapping")
        identity = db.scalar(select(PhoneIdentity).where(
            PhoneIdentity.phone_e164 == store.get("owner_phone"),
        ))
        if (
            identity is None
            or identity.tenant_id != tenant.id
            or identity.user_id != owner.id
            or identity.status != "approved"
            or identity.opt_out
        ):
            raise RuntimeError("phone_identity_invariant_failed")
        connection = db.scalar(select(BumpaConnection).where(
            BumpaConnection.tenant_id == tenant.id,
        ))
        if (
            connection is None
            or connection.status != "active"
            or connection.provider != "bumpa"
            or connection.scope_type != "business_id"
            or connection.scope_id != store.get("business_id")
            or hmac.compare_digest(connection.encrypted_api_key, store.get("api_key", ""))
        ):
            raise RuntimeError("bumpa_connection_invariant_failed")
        try:
            decrypted = cipher.decrypt(connection.encrypted_api_key)
        except (ValueError, UnicodeDecodeError) as exc:
            raise RuntimeError("bumpa_key_decryption_failed") from exc
        if not hmac.compare_digest(decrypted, store.get("api_key", "")):
            raise RuntimeError("bumpa_key_invariant_failed")
        applied_audit = db.scalar(select(AuditLog.id).where(
            AuditLog.tenant_id == tenant.id,
            AuditLog.action == "tenant.onboarding.applied",
        ).limit(1))
        if applied_audit is None:
            raise RuntimeError("onboarding_audit_missing")
        tenant_ids.add(tenant.id)
        owner_ids.add(owner.id)
        if owner.id == operator.id:
            dual_role_count += 1

    if len(tenant_ids) != 5 or len(owner_ids) != 5 or dual_role_count != 1:
        raise RuntimeError("cross_store_invariant_failed")
    print(json.dumps({
        "status": "ok",
        "tenants": len(tenant_ids),
        "owners": len(owner_ids),
        "owner_memberships": 5,
        "phone_identities": 5,
        "bumpa_connections": 5,
        "dual_role_count": dual_role_count,
    }, sort_keys=True))
""".strip()


def _read_inputs(path: Path, *, allow_operator_owner_overlap: bool = False) -> Inputs:
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            os.close(descriptor)
            raise OpsError("secret_file_not_regular")
        mode = metadata.st_mode & 0o777
        if mode & 0o077:
            os.close(descriptor)
            raise OpsError("secret_file_permissions_too_open")
        with os.fdopen(descriptor, "r", encoding="utf-8") as secret_file:
            text = secret_file.read(1_000_001)
        if len(text) > 1_000_000:
            raise OpsError("secret_file_too_large")
    except (OSError, UnicodeError) as exc:
        raise OpsError("secret_file_unavailable") from exc
    text = re.sub(r"\\([_={}\[\]+])", r"\1", text)
    globals_: dict[str, str] = {}
    for match in re.finditer(r"(?m)^([A-Z][A-Z0-9_]+)\s*=\s*(\S.*?)\s*$", text):
        key = match.group(1)
        if key in globals_:
            raise OpsError("duplicate_secret_assignment")
        globals_[key] = match.group(2).strip()

    operator = globals_.get("OPERATOR_PHONE_E164", "")
    if not E164_RE.fullmatch(operator):
        raise OpsError("operator_phone_missing_or_invalid")

    array_match = re.search(r"(?ms)(?:^|\n)\s*\[\s*\n(.*?)\n\s*\](?:\s*\n|$)", text)
    if not array_match:
        raise OpsError("bumpa_credential_list_missing")

    def reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise OpsError("duplicate_bumpa_credential_key")
            result[key] = value
        return result

    try:
        payloads = json.loads(
            "[" + array_match.group(1) + "]",
            object_pairs_hook=reject_duplicate_json_keys,
        )
    except OpsError:
        raise
    except json.JSONDecodeError as exc:
        raise OpsError("bumpa_credential_list_invalid") from exc
    if not isinstance(payloads, list) or len(payloads) != 5:
        raise OpsError("expected_exactly_five_bumpa_stores")

    mappings: dict[str, str] = {}
    for match in re.finditer(r"(?m)^(\d{3,})\s*=\s*(\+[1-9][\d ()-]{7,20})\s*$", text):
        business_id = match.group(1)
        if business_id in mappings:
            raise OpsError("duplicate_owner_mapping")
        mappings[business_id] = "+" + re.sub(r"\D", "", match.group(2))

    stores: list[Store] = []
    for index, payload in enumerate(payloads, start=1):
        if not isinstance(payload, dict):
            raise OpsError("bumpa_credential_entry_invalid")
        api_key = payload.get("secret_key")
        raw_business_id = payload.get("business_id")
        business_id = (
            str(raw_business_id) if isinstance(raw_business_id, (str, int)) else ""
        )
        owner_phone = mappings.get(business_id, "")
        if not isinstance(api_key, str) or len(api_key) < 8:
            raise OpsError("bumpa_api_key_missing_or_invalid")
        if not BUSINESS_ID_RE.fullmatch(business_id):
            raise OpsError("bumpa_business_id_missing_or_invalid")
        if not E164_RE.fullmatch(owner_phone):
            raise OpsError("owner_phone_missing_or_invalid")
        stores.append(Store(index, api_key, business_id, owner_phone))

    business_ids = {store.business_id for store in stores}
    api_keys = {store.api_key for store in stores}
    owners = {store.owner_phone for store in stores}
    if len(business_ids) != 5 or len(api_keys) != 5 or len(owners) != 5:
        raise OpsError("store_credentials_or_owners_not_unique")
    operator_owner_overlap_count = sum(
        store.owner_phone == operator for store in stores
    )
    if allow_operator_owner_overlap:
        if operator_owner_overlap_count != 1:
            raise OpsError("operator_owner_overlap_must_match_exactly_one_owner")
    elif operator_owner_overlap_count:
        raise OpsError("operator_phone_must_differ_from_all_owners")
    return Inputs(operator, tuple(stores))


def _ssh(
    host: str, remote_command: str, stdin: bytes = b"", timeout: int = 300
) -> subprocess.CompletedProcess[bytes]:
    if not SSH_HOST_RE.fullmatch(host):
        raise OpsError("ssh_host_invalid")
    command = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "IdentityFile=~/.ssh/id_ed25519",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "ConnectTimeout=10",
        host,
        remote_command,
    ]
    try:
        return subprocess.run(  # noqa: S603 - validated SSH host and fixed executable
            command,
            input=stdin,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise OpsError("ssh_execution_failed") from exc


def _remote_api_python(
    host: str, program: str, stdin: bytes, timeout: int = 60
) -> bytes:
    compose = (
        "docker compose --env-file .env.production -f compose.yaml -f compose.prod.yaml"
    )
    inner = f"cd {shlex.quote(REMOTE_ROOT)} && {compose} exec -T api python -c {shlex.quote(program)}"
    remote = "sudo -u bumpabestie -H bash -lc " + shlex.quote(inner)
    completed = _ssh(host, remote, stdin, timeout)
    if completed.returncode != 0:
        raise OpsError("remote_api_python_failed")
    return completed.stdout


def _onboard_bundle(inputs: Inputs, store: Store, *, apply: bool) -> dict[str, Any]:
    is_dual_role = hmac.compare_digest(store.owner_phone, inputs.operator_phone)
    platform_admin_name = "Bumpa Bestie Platform Admin"
    bundle: dict[str, Any] = {
        "tenant": {"slug": store.slug, "name": store.name},
        "owner": {
            "name": platform_admin_name
            if is_dual_role
            else f"Store {store.index} Owner",
            "phone_e164": store.owner_phone,
        },
        "operator": {
            "phone_e164": inputs.operator_phone,
            "name": platform_admin_name,
            "bootstrap_if_missing": True,
            "platform_role": "superadmin",
        },
        "bumpa": {"api_key": store.api_key, "business_id": store.business_id},
        "apply": apply,
    }
    if apply:
        bundle["confirmation"] = f"APPLY {store.slug}"
    return bundle


def _validate_onboard_result(raw: bytes, *, apply: bool) -> dict[str, Any]:
    try:
        payload = json.loads(raw)
        ids = payload["ids"]
        counts = payload["counts"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise OpsError("onboarding_result_invalid") from exc
    if not isinstance(ids, dict) or not isinstance(counts, dict):
        raise OpsError("onboarding_result_invalid")
    if set(counts) != EXPECTED_COUNT_KEYS or any(
        not isinstance(value, int) or isinstance(value, bool) or value < 0
        for value in counts.values()
    ):
        raise OpsError("onboarding_counts_invalid")
    if apply:
        if set(ids) != EXPECTED_ID_KEYS or any(
            not isinstance(value, str) or not UUID_RE.fullmatch(value)
            for value in ids.values()
        ):
            raise OpsError("onboarding_ids_invalid")
        if counts["applied"] != 1 or counts["dry_run"] != 0:
            raise OpsError("onboarding_apply_not_confirmed")
    elif ids or counts["applied"] != 0 or counts["dry_run"] != 1:
        raise OpsError("onboarding_dry_run_not_confirmed")
    return {"ids": ids, "counts": counts}


def _run_onboard_cli(host: str, bundle: dict[str, Any]) -> dict[str, Any]:
    compose = (
        "docker compose --env-file .env.production -f compose.yaml -f compose.prod.yaml"
    )
    inner = f"cd {shlex.quote(REMOTE_ROOT)} && {compose} exec -T api python -m app.cli.onboard"
    remote = "sudo -u bumpabestie -H bash -lc " + shlex.quote(inner)
    payload = json.dumps(bundle, separators=(",", ":")).encode()
    completed = _ssh(host, remote, payload, timeout=90)
    if completed.returncode != 0:
        raise OpsError("onboarding_cli_rejected_bundle")
    return _validate_onboard_result(completed.stdout, apply=bool(bundle["apply"]))


def plan_onboarding(inputs: Inputs, host: str) -> None:
    for store in inputs.stores:
        result = _run_onboard_cli(host, _onboard_bundle(inputs, store, apply=False))
        counts = result["counts"]
        print(
            f"onboard_dry_run_{store.index}=ok;created={counts['created']};"
            f"updated={counts['updated']};unchanged={counts['unchanged']}"
        )


def apply_onboarding(inputs: Inputs, host: str) -> dict[str, str]:
    tenant_ids: dict[str, str] = {}
    for store in inputs.stores:
        result = _run_onboard_cli(host, _onboard_bundle(inputs, store, apply=True))
        ids = result["ids"]
        counts = result["counts"]
        tenant_ids[store.slug] = ids["tenant_id"]
        print(
            f"onboard_apply_{store.index}=ok;tenant_id={ids['tenant_id']};"
            f"owner_user_id={ids['owner_user_id']};created={counts['created']};"
            f"updated={counts['updated']};unchanged={counts['unchanged']}"
        )
    return tenant_ids


def audit_onboarding(inputs: Inputs, host: str) -> None:
    expected = {
        "operator_phone": inputs.operator_phone,
        "stores": [
            {
                "slug": store.slug,
                "business_id": store.business_id,
                "api_key": store.api_key,
                "owner_phone": store.owner_phone,
            }
            for store in inputs.stores
        ],
    }
    raw = _remote_api_python(
        host,
        AUDIT_ONBOARDING_PROGRAM,
        json.dumps(expected, separators=(",", ":")).encode(),
    )
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise OpsError("onboarding_audit_result_invalid") from exc
    expected_result = {
        "status": "ok",
        "tenants": 5,
        "owners": 5,
        "owner_memberships": 5,
        "phone_identities": 5,
        "bumpa_connections": 5,
        "dual_role_count": 1,
    }
    if result != expected_result:
        raise OpsError("onboarding_audit_failed")
    print(
        "onboarding_audit=ok;tenants=5;owners=5;owner_memberships=5;"
        "phone_identities=5;bumpa_connections=5;dual_role_count=1"
    )


def onboard(inputs: Inputs, host: str) -> dict[str, str]:
    plan_onboarding(inputs, host)
    return apply_onboarding(inputs, host)


def _issue_session(
    host: str, *, phone: str, kind: str, tenant_id: str | None = None
) -> tuple[str, str]:
    request = {"phone_e164": phone, "kind": kind, "tenant_id": tenant_id}
    raw = _remote_api_python(host, ISSUE_SESSION_PROGRAM, json.dumps(request).encode())
    try:
        payload = json.loads(raw)
        token = payload["token"]
        session_id = payload["session_id"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise OpsError("session_issue_result_invalid") from exc
    if not isinstance(token, str) or len(token) < 64:
        raise OpsError("session_token_invalid")
    if not isinstance(session_id, str) or not UUID_RE.fullmatch(session_id):
        raise OpsError("session_id_invalid")
    return token, session_id


def _revoke_session(host: str, token: str) -> None:
    raw = _remote_api_python(host, REVOKE_SESSION_PROGRAM, token.encode())
    if raw.strip() != b"ok":
        raise OpsError("session_revoke_failed")


def _api_request(
    base: str,
    method: str,
    path: str,
    token: str,
    *,
    tenant_id: str | None = None,
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 45,
) -> Any:
    body = (
        json.dumps(payload, separators=(",", ":")).encode()
        if payload is not None
        else None
    )
    request_headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if body is not None:
        request_headers["Content-Type"] = "application/json"
    if tenant_id:
        request_headers["X-Tenant-ID"] = tenant_id
    if headers:
        request_headers.update(headers)
    # Cloudflare Browser Integrity rejects urllib's implicit Python signature.
    # Pin a stable, non-secret operations identity for every request, including
    # calls that supply additional headers such as Idempotency-Key.
    request_headers["User-Agent"] = OPS_USER_AGENT
    request = urllib.request.Request(  # noqa: S310 - API base is HTTPS-validated
        base.rstrip("/") + path, data=body, headers=request_headers, method=method
    )

    class RejectRedirects(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args: Any, **kwargs: Any) -> None:
            return None

    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        RejectRedirects(),
    )
    try:
        with opener.open(request, timeout=timeout) as response:
            raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise OpsError("api_response_too_large")
    except urllib.error.HTTPError as exc:
        exc.read(65_536)
        raise OpsError(f"api_http_{exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise OpsError("api_request_failed") from exc
    try:
        return json.loads(raw) if raw else None
    except json.JSONDecodeError as exc:
        raise OpsError("api_response_invalid") from exc


def _validate_api_base(value: str) -> None:
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise OpsError("api_base_invalid") from exc
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or (port is not None and port != 443)
    ):
        raise OpsError("api_base_must_be_https")


def _validate_completed_sync_run(
    completed_run: dict[str, Any],
) -> tuple[str, str, int, int, int]:
    run_id = completed_run.get("id")
    status = completed_run.get("status")
    completion_quality = completed_run.get("completion_quality")
    partial_reason = completed_run.get("partial_reason")
    orders_availability = completed_run.get("orders_availability")
    orders_count = completed_run.get("orders_count")
    datasets = completed_run.get("dataset_results")
    if not isinstance(run_id, str) or not UUID_RE.fullmatch(run_id):
        raise OpsError("bumpa_sync_run_id_invalid")
    if completed_run.get("error") is not None:
        raise OpsError("bumpa_sync_run_error_present")
    if orders_availability != "available":
        raise OpsError("bumpa_sync_orders_unavailable")
    if (
        isinstance(orders_count, bool)
        or not isinstance(orders_count, int)
        or orders_count < 0
    ):
        raise OpsError("bumpa_sync_orders_count_invalid")
    if not isinstance(datasets, dict) or set(datasets) != EXPECTED_SYNC_DATASETS:
        raise OpsError("bumpa_sync_dataset_set_invalid")
    if any(
        value not in {"available", "unavailable", "error"}
        for value in datasets.values()
    ):
        raise OpsError("bumpa_sync_dataset_status_invalid")
    unavailable = {key for key, value in datasets.items() if value == "unavailable"}
    errors = {key for key, value in datasets.items() if value == "error"}
    if unavailable - OPTIONAL_UNAVAILABLE_SYNC_DATASETS:
        raise OpsError("bumpa_sync_required_dataset_unavailable")
    expected_status = "partial" if unavailable or errors else "success"
    if status != expected_status:
        raise OpsError("bumpa_sync_run_status_mismatch")
    if errors:
        expected_quality = "degraded"
        expected_reason = "dataset_error"
    elif unavailable:
        expected_quality = "accepted_partial"
        expected_reason = "profit_not_calculable"
    else:
        expected_quality = "complete"
        expected_reason = None
    if completion_quality != expected_quality or partial_reason != expected_reason:
        # The API assigns these typed completion states only after checking each
        # provider diagnostic and proving the orders read completed safely.
        raise OpsError("bumpa_sync_completion_evidence_invalid")
    return (
        run_id,
        expected_status,
        len(datasets) - len(unavailable) - len(errors),
        len(unavailable),
        len(errors),
    )


def _tenant_ids(inputs: Inputs, host: str, api_base: str) -> dict[str, str]:
    token, session_id = _issue_session(
        host, phone=inputs.operator_phone, kind="operator"
    )
    print(f"operator_session=issued;session_id={session_id};ttl_minutes=5")
    try:
        tenants = _api_request(api_base, "GET", "/admin/tenants?limit=200", token)
    finally:
        _revoke_session(host, token)
        print(f"operator_session=revoked;session_id={session_id}")
    if not isinstance(tenants, list):
        raise OpsError("tenant_list_invalid")
    found: dict[str, str] = {}
    for row in tenants:
        if not isinstance(row, dict) or row.get("slug") not in {
            store.slug for store in inputs.stores
        }:
            continue
        tenant_id = row.get("id")
        if not isinstance(tenant_id, str) or not UUID_RE.fullmatch(tenant_id):
            raise OpsError("tenant_id_invalid")
        found[str(row["slug"])] = tenant_id
    if set(found) != {store.slug for store in inputs.stores}:
        raise OpsError("expected_onboarded_tenants_not_found")
    return found


def _reconcile_hermes(host: str) -> int:
    inner = f"cd {shlex.quote(REMOTE_ROOT)} && ENV_FILE=.env.production ./scripts/reconcile_hermes_profiles.sh"
    remote = "sudo -u bumpabestie -H bash -lc " + shlex.quote(inner)
    completed = _ssh(host, remote, timeout=360)
    if completed.returncode != 0:
        raise OpsError("hermes_reconciliation_failed")
    match = re.search(rb"Hermes profiles ready: ([0-9]+)", completed.stdout)
    if not match:
        raise OpsError("hermes_reconciliation_result_invalid")
    return int(match.group(1))


def provision_hermes(
    inputs: Inputs, host: str, api_base: str, *, live_chat: bool
) -> None:
    tenant_ids = _tenant_ids(inputs, host, api_base)
    token, session_id = _issue_session(
        host, phone=inputs.operator_phone, kind="operator"
    )
    print(f"operator_session=issued;session_id={session_id};ttl_minutes=5")
    try:
        for store in inputs.stores:
            tenant_id = tenant_ids[store.slug]
            profile = _api_request(
                api_base, "POST", f"/admin/tenants/{tenant_id}/hermes-profile", token
            )
            profile_id = profile.get("id") if isinstance(profile, dict) else None
            if not isinstance(profile_id, str) or not UUID_RE.fullmatch(profile_id):
                raise OpsError("hermes_profile_result_invalid")
            print(
                f"hermes_provision_{store.index}=ok;tenant_id={tenant_id};profile_id={profile_id}"
            )
    finally:
        _revoke_session(host, token)
        print(f"operator_session=revoked;session_id={session_id}")

    count = _reconcile_hermes(host)
    if count < 5:
        raise OpsError("hermes_reconciliation_profile_count_too_low")
    print(f"hermes_reconciliation=ok;profile_count={count}")

    for store in inputs.stores:
        tenant_id = tenant_ids[store.slug]
        token, session_id = _issue_session(
            host, phone=store.owner_phone, kind="owner", tenant_id=tenant_id
        )
        print(
            f"owner_session_{store.index}=issued;session_id={session_id};ttl_minutes=5"
        )
        try:
            readiness = _api_request(
                api_base, "GET", "/hermes/profile/readiness", token, tenant_id=tenant_id
            )
            if not isinstance(readiness, dict) or readiness.get("status") != "ready":
                raise OpsError("hermes_readiness_invalid")
            latency = readiness.get("latency_ms")
            if not isinstance(latency, int) or isinstance(latency, bool) or latency < 0:
                raise OpsError("hermes_readiness_invalid")
            print(
                f"hermes_readiness_{store.index}=ready;tenant_id={tenant_id};latency_ms={latency}"
            )
            if live_chat:
                response = _api_request(
                    api_base,
                    "POST",
                    "/chat/web",
                    token,
                    tenant_id=tenant_id,
                    payload={
                        "message": "Reply with a brief confirmation that this private business profile is ready.",
                        "client_message_id": f"production-hermes-canary-{uuid4()}",
                    },
                    timeout=120,
                )
                required = (
                    "conversation_id",
                    "inbound_message_id",
                    "outbound_message_id",
                )
                if (
                    not isinstance(response, dict)
                    or any(
                        not isinstance(response.get(key), str)
                        or not UUID_RE.fullmatch(response[key])
                        for key in required
                    )
                    or not isinstance(response.get("answer"), str)
                    or not response["answer"].strip()
                ):
                    raise OpsError("hermes_chat_canary_invalid")
                print(
                    f"hermes_chat_{store.index}=ok;tenant_id={tenant_id};"
                    f"conversation_id={response['conversation_id']};answer_nonempty=true"
                )
        finally:
            _revoke_session(host, token)
            print(f"owner_session_{store.index}=revoked;session_id={session_id}")


def bumpa_sync_canaries(inputs: Inputs, host: str, api_base: str) -> None:
    tenant_ids = _tenant_ids(inputs, host, api_base)
    for store in inputs.stores:
        tenant_id = tenant_ids[store.slug]
        token, session_id = _issue_session(
            host, phone=store.owner_phone, kind="owner", tenant_id=tenant_id
        )
        print(
            f"owner_session_{store.index}=issued;session_id={session_id};ttl_minutes=5"
        )
        try:
            queued = _api_request(
                api_base,
                "POST",
                "/bumpa/sync/latest",
                token,
                tenant_id=tenant_id,
                payload=None,
                headers={"Idempotency-Key": f"production-canary-{uuid4()}"},
            )
            if not isinstance(queued, dict) or queued.get("status") != "queued":
                raise OpsError("bumpa_sync_not_queued")
            job_id = queued.get("job_id")
            if not isinstance(job_id, str) or not UUID_RE.fullmatch(job_id):
                raise OpsError("bumpa_sync_job_id_invalid")
            requested_from = queued.get("requested_from")
            requested_to = queued.get("requested_to")
            if (
                not isinstance(requested_from, str)
                or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", requested_from)
                or not isinstance(requested_to, str)
                or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", requested_to)
            ):
                raise OpsError("bumpa_sync_requested_range_invalid")
            print(
                f"bumpa_sync_{store.index}=queued;tenant_id={tenant_id};job_id={job_id}"
            )

            sync_run_id: str | None = None
            deadline = time.monotonic() + 240
            while time.monotonic() < deadline:
                job = _api_request(
                    api_base,
                    "GET",
                    f"/bumpa/sync-jobs/{job_id}",
                    token,
                    tenant_id=tenant_id,
                )
                if not isinstance(job, dict) or job.get("job_id") != job_id:
                    raise OpsError("bumpa_sync_job_status_invalid")
                if (
                    job.get("requested_from") != requested_from
                    or job.get("requested_to") != requested_to
                ):
                    raise OpsError("bumpa_sync_job_range_mismatch")
                if job.get("status") == "succeeded":
                    candidate_run_id = job.get("sync_run_id")
                    if not isinstance(candidate_run_id, str) or not UUID_RE.fullmatch(
                        candidate_run_id
                    ):
                        raise OpsError("bumpa_sync_job_result_invalid")
                    sync_run_id = candidate_run_id
                    break
                if job.get("status") in {"dead_letter", "cancelled"}:
                    raise OpsError("bumpa_sync_job_failed")
                time.sleep(3)
            if sync_run_id is None:
                raise OpsError("bumpa_sync_canary_timed_out")

            runs = _api_request(
                api_base, "GET", "/bumpa/sync-runs", token, tenant_id=tenant_id
            )
            if not isinstance(runs, list):
                raise OpsError("bumpa_sync_run_list_invalid")
            completed_run = next(
                (
                    row
                    for row in runs
                    if isinstance(row, dict) and row.get("id") == sync_run_id
                ),
                None,
            )
            if completed_run is None:
                raise OpsError("bumpa_sync_correlated_run_not_found")
            if (
                completed_run.get("requested_from") != requested_from
                or completed_run.get("requested_to") != requested_to
            ):
                raise OpsError("bumpa_sync_run_range_mismatch")
            run_id, status, available, unavailable, errors = (
                _validate_completed_sync_run(completed_run)
            )
            print(
                f"bumpa_sync_{store.index}={status};tenant_id={tenant_id};run_id={run_id};"
                f"datasets_available={available};datasets_unavailable={unavailable};"
                f"datasets_error={errors};"
                f"datasets_total={len(EXPECTED_SYNC_DATASETS)}"
            )
        finally:
            _revoke_session(host, token)
            print(f"owner_session_{store.index}=revoked;session_id={session_id}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Secure Bumpa Bestie production operations helper"
    )
    parser.add_argument(
        "action", choices=("check", "plan", "onboard", "audit", "hermes", "sync", "all")
    )
    parser.add_argument("--secret-file", type=Path, default=DEFAULT_SECRET_FILE)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--api-base", default=DEFAULT_API_BASE)
    parser.add_argument(
        "--live-chat",
        action="store_true",
        help="Run one real Hermes/Claude response canary per tenant after readiness",
    )
    parser.add_argument(
        "--allow-operator-owner-overlap",
        action="store_true",
        help=(
            "Explicitly allow the operator to also own exactly one configured store; "
            "the default rejects every operator/owner overlap"
        ),
    )
    args = parser.parse_args()
    try:
        _validate_api_base(args.api_base)
        inputs = _read_inputs(
            args.secret_file,
            allow_operator_owner_overlap=args.allow_operator_owner_overlap,
        )
        overlap_count = sum(
            store.owner_phone == inputs.operator_phone for store in inputs.stores
        )
        print(
            "preflight=ok;stores=5;owners=5;"
            f"operator_owner_overlap_count={overlap_count};"
            f"operator_owner_overlap_explicitly_allowed={str(args.allow_operator_owner_overlap).lower()}"
        )
        if args.action == "plan":
            plan_onboarding(inputs, args.host)
        if args.action in {"onboard", "all"}:
            onboard(inputs, args.host)
            audit_onboarding(inputs, args.host)
        if args.action == "audit":
            audit_onboarding(inputs, args.host)
        if args.action in {"hermes", "all"}:
            provision_hermes(inputs, args.host, args.api_base, live_chat=args.live_chat)
        if args.action in {"sync", "all"}:
            bumpa_sync_canaries(inputs, args.host, args.api_base)
        return 0
    except OpsError as exc:
        print(f"operation=failed;reason={exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
