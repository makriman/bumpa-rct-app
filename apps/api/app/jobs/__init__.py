"""Public API for durable jobs and worker-side handler registration.

Runtime exports are resolved lazily so operational commands such as
``python -m app.jobs.health`` do not load SQLAlchemy and the application models.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.jobs.runtime import PermanentJobError, enqueue_job, register_handler

__all__ = ["PermanentJobError", "enqueue_job", "register_handler"]


def __getattr__(name: str) -> object:
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    from app.jobs import runtime

    return getattr(runtime, name)
