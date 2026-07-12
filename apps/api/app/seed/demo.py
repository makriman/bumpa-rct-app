from app.core.config import get_settings
from app.db.session import SessionLocal, create_schema, set_security_context
from app.services.seed import seed_demo


def main() -> None:
    settings = get_settings()
    if not settings.is_local:
        raise RuntimeError("Demo seeding is disabled outside local and test environments")
    create_schema()
    with SessionLocal() as db:
        set_security_context(db, privileged=True)
        seed_demo(db, settings)
    print("Demo data is ready. Owner: +2348012345678, OTP: 246810")


if __name__ == "__main__":
    main()
