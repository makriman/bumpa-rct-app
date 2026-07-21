from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, inspect

import app.core.config as config_module
from alembic import command

API_ROOT = Path(__file__).parents[1]
INDEX_NAME = "ix_conversation_tenant_user_updated_id"


def _config(database_url: str, monkeypatch: pytest.MonkeyPatch) -> Config:
    monkeypatch.setattr(
        config_module,
        "get_settings",
        lambda: SimpleNamespace(database_url=database_url, migration_database_url=None),
    )
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "alembic"))
    return config


def _conversation_indexes(database_url: str) -> dict[str, dict[str, object]]:
    engine = create_engine(database_url)
    try:
        return {str(index["name"]): index for index in inspect(engine).get_indexes("conversations")}
    finally:
        engine.dispose()


def test_chat_history_index_upgrade_downgrade_and_reupgrade(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'chat-pagination.db'}"
    config = _config(database_url, monkeypatch)

    command.upgrade(config, "0015_bumpa_store_context")
    assert INDEX_NAME not in _conversation_indexes(database_url)

    command.upgrade(config, "0016_chat_pagination")
    upgraded = _conversation_indexes(database_url)
    assert upgraded[INDEX_NAME]["column_names"] == [
        "tenant_id",
        "user_id",
        "updated_at",
        "id",
    ]

    command.downgrade(config, "0015_bumpa_store_context")
    assert INDEX_NAME not in _conversation_indexes(database_url)

    command.upgrade(config, "head")
    assert INDEX_NAME in _conversation_indexes(database_url)
