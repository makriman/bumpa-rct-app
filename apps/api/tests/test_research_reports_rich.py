from __future__ import annotations

import csv
import hashlib
import io
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader
from sqlalchemy import func, select

from app.core.config import get_settings
from app.db.models import Artifact, ResearchReport
from app.db.session import SessionLocal
from app.providers.local import LocalArtifactStore
from app.schemas import ReportCreate
from app.services.reports import REPORT_TYPES, generate_report, validated_filters
from tests.conftest import auth_headers


@pytest.mark.parametrize("report_type", sorted(REPORT_TYPES))
def test_complete_report_catalogue_is_schema_valid(report_type: str) -> None:
    assert ReportCreate(report_type=report_type).report_type == report_type


def test_report_filters_are_narrow_and_date_bounded() -> None:
    assert (
        validated_filters(
            {
                "tenant_pseudonym": "tenant_demo_123",
                "channel": "whatsapp",
                "business_function": "sales",
                "ai_help_type": "diagnosis",
                "date_from": "2026-01-01",
                "date_to": "2026-03-31",
            }
        )["channel"]
        == "whatsapp"
    )
    with pytest.raises(ValueError, match="Unsupported report filters"):
        validated_filters({"phone": "+2348000000000"})
    with pytest.raises(ValueError, match="either tenant_id or tenant_pseudonym"):
        validated_filters({"tenant_id": "one", "tenant_pseudonym": "two"})
    with pytest.raises(ValueError, match="cannot precede"):
        validated_filters({"date_from": "2026-07-02", "date_to": "2026-07-01"})
    with pytest.raises(ValueError, match="cannot exceed 366"):
        validated_filters({"date_from": "2024-01-01", "date_to": "2026-01-01"})


def test_structured_multiformat_report_is_parseable_private_and_formula_safe(
    client: TestClient,
) -> None:
    owner = auth_headers(client, "+2348012345678")
    message = client.post(
        "/v1/chat/web",
        headers=owner,
        json={
            "message": "=SUM(1,1) Compare sales for ada.report@example.com",
            "client_message_id": "rich-report-formula-source",
        },
    )
    assert message.status_code == 200, message.text

    researcher = auth_headers(client, "+2348099990002")
    questions = client.get("/v1/research/questions", headers=researcher)
    assert questions.status_code == 200, questions.text
    assert questions.json()
    assert {row["event_type"] for row in questions.json()} == {"user_message_received"}
    assert all(isinstance(row["raw_text_present"], bool) for row in questions.json())
    assistant_events = client.get(
        "/v1/research/events",
        headers=researcher,
        params={"event_type": "assistant_response_sent"},
    )
    assert assistant_events.status_code == 200, assistant_events.text
    assert assistant_events.json()
    assert {row["event_type"] for row in assistant_events.json()} == {"assistant_response_sent"}
    created = client.post(
        "/v1/research/reports",
        headers=researcher,
        json={
            "report_type": "question_taxonomy",
            "filters": {"channel": "web"},
            "formats": ["pdf", "jsonl", "csv"],
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["artifact_kind"] == "report"
    report_id = created.json()["id"]
    report_inventory = client.get(
        "/v1/research/reports", headers=researcher, params={"artifact_kind": "report"}
    )
    export_inventory = client.get(
        "/v1/research/reports", headers=researcher, params={"artifact_kind": "export"}
    )
    assert report_id in {row["id"] for row in report_inventory.json()}
    assert report_id not in {row["id"] for row in export_inventory.json()}

    detail = client.get(f"/v1/research/reports/{report_id}", headers=researcher)
    assert detail.status_code == 200, detail.text
    assert detail.json()["report_type"] == "question_taxonomy"
    assert {item["format"] for item in detail.json()["artifacts"]} == {
        "csv",
        "jsonl",
        "pdf",
    }

    csv_response = client.get(f"/v1/research/reports/{report_id}/download/csv", headers=researcher)
    assert csv_response.status_code == 200, csv_response.text
    assert csv_response.content.startswith(b"\xef\xbb\xbf")
    assert "'=SUM(1,1)" in csv_response.text
    assert "ada.report@example.com" not in csv_response.text
    parsed_rows = list(csv.DictReader(io.StringIO(csv_response.text.lstrip("\ufeff"))))
    assert parsed_rows and "tenant_pseudonym" in parsed_rows[0]

    jsonl_response = client.get(
        f"/v1/research/reports/{report_id}/download/jsonl", headers=researcher
    )
    assert jsonl_response.status_code == 200, jsonl_response.text
    records = [json.loads(line) for line in jsonl_response.text.splitlines()]
    assert records[0]["record_type"] == "report_metadata"
    assert records[0]["disclosure_mode"] == "anonymized_redacted"
    serialized = json.dumps(records)
    assert "ada.report@example.com" not in serialized
    assert "tenant_id" not in records[0]["filters"]

    pdf_response = client.get(f"/v1/research/reports/{report_id}/download/pdf", headers=researcher)
    assert pdf_response.status_code == 200, pdf_response.text
    reader = PdfReader(io.BytesIO(pdf_response.content))
    pdf_text = "\n".join(page.extract_text() or "" for page in reader.pages)
    assert len(reader.pages) >= 3
    for heading in (
        "Executive summary",
        "Research question",
        "Question taxonomy",
        "Outcome signals",
        "Operational recommendations",
        "Caveats",
        "Record appendix",
    ):
        assert heading in pdf_text
    assert "ada.report@example.com" not in pdf_text


def test_raw_export_is_superadmin_reason_gated_and_independently_gated_on_download(
    client: TestClient,
) -> None:
    owner = auth_headers(client, "+2348012345678")
    source = "Confidential source phrase for raw export validation"
    sent = client.post(
        "/v1/chat/web",
        headers=owner,
        json={"message": source, "client_message_id": "raw-export-source"},
    )
    assert sent.status_code == 200, sent.text

    researcher = auth_headers(client, "+2348099990002")
    payload = {"report_type": "raw_export_package", "formats": ["jsonl"]}
    forbidden = client.post(
        "/v1/research/exports",
        headers={**researcher, "X-Access-Reason": "Approved research validation"},
        json=payload,
    )
    assert forbidden.status_code == 403

    superadmin = auth_headers(client, "+2348099990000")
    assert client.post("/v1/research/exports", headers=superadmin, json=payload).status_code == 422
    unsafe = client.post(
        "/v1/research/exports",
        headers={**superadmin, "X-Access-Reason": "Email reviewer@example.com"},
        json=payload,
    )
    assert unsafe.status_code == 422
    secret_reason = client.post(
        "/v1/research/exports",
        headers={**superadmin, "X-Access-Reason": "API key=sk-ant-do-not-store-this"},
        json=payload,
    )
    assert secret_reason.status_code == 422
    created = client.post(
        "/v1/research/exports",
        headers={**superadmin, "X-Access-Reason": "Validate approved research source records"},
        json=payload,
    )
    assert created.status_code == 200, created.text
    assert created.json()["artifact_kind"] == "export"
    report_id = created.json()["id"]
    export_inventory = client.get(
        "/v1/research/reports", headers=superadmin, params={"artifact_kind": "export"}
    )
    assert report_id in {row["id"] for row in export_inventory.json()}

    endpoint = f"/v1/research/reports/{report_id}/download/jsonl"
    assert client.get(endpoint, headers=superadmin).status_code == 422
    downloaded = client.get(
        endpoint,
        headers={**superadmin, "X-Access-Reason": "Review approved source export package"},
    )
    assert downloaded.status_code == 200, downloaded.text
    assert source in downloaded.text
    assert downloaded.headers["cache-control"].startswith("no-store")
    assert downloaded.headers["x-content-type-options"] == "nosniff"


def test_successful_report_retry_verifies_integrity_and_rebuilds_corruption(
    client: TestClient,
) -> None:
    researcher = auth_headers(client, "+2348099990002")
    created = client.post(
        "/v1/research/reports",
        headers=researcher,
        json={"report_type": "sme_usage", "formats": ["jsonl"]},
    )
    assert created.status_code == 201, created.text
    report_id = created.json()["id"]
    settings = get_settings()
    store = LocalArtifactStore(settings.artifact_root)

    with SessionLocal() as db:
        report = db.get(ResearchReport, report_id)
        assert report is not None
        artifact = db.scalar(select(Artifact).where(Artifact.report_id == report_id))
        assert artifact is not None
        original_id = artifact.id
        original_bytes = store.get(artifact.storage_key)
        generate_report(
            db,
            store,
            report,
            ["jsonl"],
            pseudonym_secret=settings.research_pseudonym_key,
        )
        assert (
            db.scalar(
                select(func.count()).select_from(Artifact).where(Artifact.report_id == report_id)
            )
            == 1
        )
        assert db.scalar(select(Artifact.id).where(Artifact.report_id == report_id)) == original_id
        assert store.get(artifact.storage_key) == original_bytes

        artifact_path = (Path(settings.artifact_root) / artifact.storage_key).resolve()
        artifact_path.write_bytes(b"corrupted")
        generate_report(
            db,
            store,
            report,
            ["jsonl"],
            pseudonym_secret=settings.research_pseudonym_key,
        )
        rebuilt = db.scalar(select(Artifact).where(Artifact.report_id == report_id))
        assert rebuilt is not None
        rebuilt_bytes = store.get(rebuilt.storage_key)
        assert rebuilt_bytes != b"corrupted"
        assert json.loads(rebuilt_bytes.splitlines()[0])["record_type"] == "report_metadata"
        assert rebuilt.byte_size == len(rebuilt_bytes)
        assert rebuilt.checksum_sha256 == hashlib.sha256(rebuilt_bytes).hexdigest()


def test_multiformat_generation_failure_leaves_no_partial_package(tmp_path: Path) -> None:
    class FailingStore(LocalArtifactStore):
        def put(self, key: str, content: bytes) -> tuple[str, int, str]:
            if key.endswith(".pdf"):
                raise OSError("simulated artifact backend failure")
            return super().put(key, content)

    settings = get_settings()
    with SessionLocal() as db:
        report = ResearchReport(
            report_type="sme_usage",
            artifact_kind="report",
            filters={"channel": "web"},
            status="queued",
            title="Atomic package test",
        )
        db.add(report)
        db.commit()
        report_id = report.id
        with pytest.raises(OSError, match="simulated artifact backend failure"):
            generate_report(
                db,
                FailingStore(tmp_path),
                report,
                ["pdf", "csv"],
                pseudonym_secret=settings.research_pseudonym_key,
            )
        db.refresh(report)
        assert report.status == "failed"
        assert db.scalar(select(Artifact).where(Artifact.report_id == report_id)) is None
        assert not list(tmp_path.rglob("*.*"))


def test_download_fails_closed_when_artifact_bytes_are_corrupt(client: TestClient) -> None:
    researcher = auth_headers(client, "+2348099990002")
    created = client.post(
        "/v1/research/reports",
        headers=researcher,
        json={"report_type": "sme_usage", "formats": ["csv"]},
    )
    assert created.status_code == 201, created.text
    report_id = created.json()["id"]
    settings = get_settings()
    with SessionLocal() as db:
        artifact = db.scalar(select(Artifact).where(Artifact.report_id == report_id))
        assert artifact is not None
        (Path(settings.artifact_root) / artifact.storage_key).write_bytes(b"tampered")
    response = client.get(
        f"/v1/research/reports/{report_id}/download/csv",
        headers=researcher,
    )
    assert response.status_code == 410
    with SessionLocal() as db:
        assert db.scalar(select(Artifact).where(Artifact.report_id == report_id)) is None
