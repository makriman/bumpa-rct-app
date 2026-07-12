from typing import Any

from sqlalchemy.orm import Session

from app.core.logging import correlation_id_var
from app.db.models import AuditLog


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
    return record
