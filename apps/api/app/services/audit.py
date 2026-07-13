from typing import Any

from sqlalchemy.orm import Session

from app.core.ids import new_id
from app.core.logging import correlation_id_var
from app.db.models import AuditLog
from app.services.research_events import (
    record_admin_action_event,
    record_hermes_profile_created_event,
)

ADMIN_RESEARCH_PREFIXES = (
    "admin.",
    "async_job.",
    "hermes.",
    "mcp.",
    "phone.",
    "platform.",
    "team.",
    "tenant.",
)
ADMIN_RESEARCH_ACTIONS = frozenset({"research.consent.changed"})
HERMES_PROFILE_CREATED_ACTIONS = frozenset({"hermes.profile.created", "hermes.profile.provisioned"})


def audit(
    db: Session,
    *,
    actor_user_id: str | None,
    action: str,
    tenant_id: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> AuditLog:
    record = AuditLog(
        id=new_id(),
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        before=before,
        after=after,
        correlation_id=correlation_id_var.get(),
    )
    db.add(record)
    if tenant_id is not None and (
        action in ADMIN_RESEARCH_ACTIONS or action.startswith(ADMIN_RESEARCH_PREFIXES)
    ):
        record_admin_action_event(
            db,
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            audit_id=record.id,
            action=action,
            resource_type=resource_type,
        )
        if action in HERMES_PROFILE_CREATED_ACTIONS:
            provider = "hermes" if action.endswith("provisioned") else "local"
            record_hermes_profile_created_event(
                db,
                tenant_id=tenant_id,
                actor_user_id=actor_user_id,
                audit_id=record.id,
                provider=provider,
            )
    return record
