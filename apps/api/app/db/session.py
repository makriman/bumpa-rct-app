from collections.abc import Generator

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

settings = get_settings()
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    connect_args={"check_same_thread": False} if settings.database_url.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


@event.listens_for(Session, "after_begin")
def reapply_postgres_security_context(
    session: Session, _transaction: object, connection: Connection
) -> None:
    """Reapply transaction-local RLS variables after commit starts a new transaction."""
    if engine.dialect.name != "postgresql" or not session.info.get("rls_configured"):
        return
    connection.execute(
        text(
            "SELECT set_config('app.current_tenant_id', :tenant_id, true), "
            "set_config('app.is_privileged', :privileged, true)"
        ),
        {
            "tenant_id": session.info.get("tenant_id", ""),
            "privileged": "true" if session.info.get("is_privileged") else "false",
        },
    )


def get_db() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def create_schema() -> None:
    from app.db import models  # noqa: F401
    from app.db.base import Base

    Base.metadata.create_all(engine)


def set_security_context(
    session: Session, *, tenant_id: str | None = None, privileged: bool = False
) -> None:
    """Set the database context consumed by Postgres RLS policies for this transaction."""
    session.info.update(
        {"rls_configured": True, "tenant_id": tenant_id or "", "is_privileged": privileged}
    )
    if engine.dialect.name == "postgresql":
        session.execute(
            text(
                "SELECT set_config('app.current_tenant_id', :tenant_id, true), "
                "set_config('app.is_privileged', :privileged, true)"
            ),
            {"tenant_id": tenant_id or "", "privileged": "true" if privileged else "false"},
        )
