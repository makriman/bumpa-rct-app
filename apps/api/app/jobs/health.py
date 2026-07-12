from __future__ import annotations

import sys

from app.jobs.runtime import AsyncRuntimeConfig, RedisWakeQueue


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in {"worker", "scheduler"}:
        raise SystemExit("Usage: python -m app.jobs.health worker|scheduler")
    config = AsyncRuntimeConfig.from_env()
    if not config.enabled or not RedisWakeQueue(config).is_healthy(sys.argv[1]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
