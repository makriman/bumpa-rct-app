"""Run synthetic HTTP onboarding/OTP checks under a disposable non-bypass role."""

import os
import secrets

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url


def main() -> None:
    admin = make_url(os.environ["DATABASE_URL"])
    if (
        os.environ.get("APP_ENV") != "test"
        or not admin.drivername.startswith("postgresql")
        or admin.host not in {"127.0.0.1", "localhost"}
        or any(
            os.environ.get(key) != "mock"
            for key in ("WHATSAPP_BACKEND", "AGENT_BACKEND", "BUMPA_BACKEND")
        )
    ):
        raise RuntimeError("Use disposable loopback PostgreSQL, test mode and mock providers")
    role = "release_auth_" + secrets.token_hex(6)
    password = secrets.token_hex(24)
    url = admin.set(drivername="postgresql").render_as_string(hide_password=False)
    with psycopg.connect(url, autocommit=True) as connection:
        connection.execute(
            sql.SQL(
                "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS"
            ).format(sql.Identifier(role), sql.Literal(password))
        )
        try:
            connection.execute(
                sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                    sql.Identifier(admin.database), sql.Identifier(role)
                )
            )
            connection.execute(
                sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(role))
            )
            connection.execute(
                sql.SQL(
                    "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {}"
                ).format(sql.Identifier(role))
            )
            connection.execute(
                sql.SQL("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {}").format(
                    sql.Identifier(role)
                )
            )
            os.environ.update(
                {
                    "DATABASE_URL": admin.set(username=role, password=password).render_as_string(
                        hide_password=False
                    ),
                    "AUTH_LOGIN_MODE": "whatsapp_otp",
                    "SEED_DEMO_DATA": "false",
                    "EXPOSE_LOCAL_OTP": "true",
                    "ASYNC_RUNTIME_ENABLED": "false",
                }
            )
            from verify_business_onboarding import main as verify

            from app.db.session import engine

            try:
                verify()
            finally:
                engine.dispose()
        finally:
            connection.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
            connection.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


if __name__ == "__main__":
    main()
