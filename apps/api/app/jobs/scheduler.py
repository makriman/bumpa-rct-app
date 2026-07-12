import logging
import signal
import time

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import create_schema

logger = logging.getLogger("bumpabestie.scheduler")
running = True


def _stop(_signum: int, _frame: object) -> None:
    global running
    running = False


def main() -> None:
    configure_logging()
    settings = get_settings()
    if not settings.is_local:
        raise RuntimeError(
            "No production scheduler adapter is configured; refusing to run local scheduler"
        )
    create_schema()
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    logger.info("scheduler_ready", extra={"mode": "local", "proactive_jobs": "disabled"})
    while running:
        time.sleep(5)
    logger.info("scheduler_stopped")


if __name__ == "__main__":
    main()
