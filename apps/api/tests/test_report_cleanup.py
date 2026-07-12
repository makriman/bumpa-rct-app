from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models import Artifact, ResearchReport
from app.services.reports import _cleanup_candidate_statement, cleanup_expired_report_artifacts


def test_cleanup_candidate_query_compiles_for_postgresql_without_distinct() -> None:
    sql = str(
        _cleanup_candidate_statement(limit=25).compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    normalized = " ".join(sql.upper().split())

    assert "SELECT DISTINCT" not in normalized
    assert "WHERE EXISTS (SELECT ARTIFACTS.ID" in normalized
    assert "LIMIT 25" in normalized


def test_cleanup_processes_one_report_once_when_it_has_multiple_artifacts(tmp_path: Path) -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    report = ResearchReport(
        report_type="cleanup-regression",
        filters={"channel": "web"},
        status="success",
        created_at=datetime(2000, 1, 1, tzinfo=UTC),
    )
    formats = ("csv", "jsonl", "pdf")

    with Session(engine) as db:
        db.add(report)
        db.flush()
        report_id = report.id
        for fmt in formats:
            storage_key = f"{report_id}/{report_id}.{fmt}"
            artifact_path = tmp_path / storage_key
            artifact_path.parent.mkdir(parents=True, exist_ok=True)
            artifact_path.write_bytes(fmt.encode())
            db.add(
                Artifact(
                    report_id=report_id,
                    format=fmt,
                    storage_key=storage_key,
                    content_type="application/octet-stream",
                    byte_size=len(fmt),
                    checksum_sha256="0" * 64,
                )
            )
        db.commit()

        result = cleanup_expired_report_artifacts(db, tmp_path, limit=1)

        assert result == {"reports_cleaned": 1, "artifacts_deleted": len(formats)}
        assert db.get(ResearchReport, report_id) is not None
        assert list(db.scalars(select(Artifact).where(Artifact.report_id == report_id))) == []
        assert all(not (tmp_path / report_id / f"{report_id}.{fmt}").exists() for fmt in formats)
