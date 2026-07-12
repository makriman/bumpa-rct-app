from uuid import UUID, uuid4


def new_id() -> str:
    return str(uuid4())


def normalize_id(value: str | UUID) -> str:
    return str(UUID(str(value)))
