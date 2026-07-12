from __future__ import annotations

import csv
import hashlib
import io
import json
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.time import utcnow
from app.db.models import Artifact, ResearchEvent, ResearchReport
from app.providers.local import LocalArtifactStore
from app.providers.redaction import csv_safe


def _rows(db: Session, filters: dict[str, Any]) -> list[dict[str, Any]]:
    statement = select(ResearchEvent).order_by(ResearchEvent.created_at.desc()).limit(10_000)
    if tenant_id := filters.get("tenant_id"):
        statement = statement.where(ResearchEvent.tenant_id == tenant_id)
    if channel := filters.get("channel"):
        statement = statement.where(ResearchEvent.channel == channel)
    if intent := filters.get("primary_intent"):
        statement = statement.where(ResearchEvent.primary_intent == intent)
    events = db.scalars(statement).all()
    return [
        {
            "timestamp": event.created_at.isoformat(),
            "tenant_pseudonym": hashlib.sha256((event.tenant_id or "unknown").encode()).hexdigest()[
                :12
            ],
            "channel": event.channel,
            "question": event.redacted_text or "",
            "primary_intent": event.primary_intent or "unclassified",
            "business_function": event.business_function or "unclassified",
            "ai_help_type": event.ai_help_type or "unclassified",
            "complexity": event.complexity or "unclassified",
            "bumpa_data_used": event.bumpa_data_used or "none",
        }
        for event in events
    ]


def _csv(rows: list[dict[str, Any]]) -> bytes:
    if not rows:
        return b""
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows({key: csv_safe(value) for key, value in row.items()} for row in rows)
    return output.getvalue().encode()


def _jsonl(rows: list[dict[str, Any]]) -> bytes:
    return (
        "\n".join(json.dumps(row, ensure_ascii=False, default=str) for row in rows) + "\n"
    ).encode()


def _pdf(rows: list[dict[str, Any]], title: str) -> bytes:
    # Minimal standards-compliant PDF; production renderer can replace this through the artifact port.
    summary = f"{title} - {len(rows)} anonymized research events"
    safe = summary.replace("(", "[").replace(")", "]")
    stream = f"BT /F1 18 Tf 72 720 Td ({safe}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    data = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(data))
        data.extend(f"{index} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(data)
    data.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        data.extend(f"{offset:010d} 00000 n \n".encode())
    data.extend(
        f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(data)


def generate_report(
    db: Session,
    store: LocalArtifactStore,
    report: ResearchReport,
    formats: Sequence[str],
) -> ResearchReport:
    report.status = "running"
    db.commit()
    rows = _rows(db, report.filters)
    title = report.title or report.report_type.replace("_", " ").title()
    for fmt in sorted(set(formats)):
        if fmt == "csv":
            content, content_type = _csv(rows), "text/csv"
        elif fmt == "jsonl":
            content, content_type = _jsonl(rows), "application/x-ndjson"
        elif fmt == "pdf":
            content, content_type = _pdf(rows, title), "application/pdf"
        else:
            continue
        key, byte_size, checksum = store.put(f"{report.id}/{report.id}.{fmt}", content)
        db.add(
            Artifact(
                report_id=report.id,
                format=fmt,
                storage_key=key,
                content_type=content_type,
                byte_size=byte_size,
                checksum_sha256=checksum,
            )
        )
    report.status = "success"
    report.summary = f"Generated an anonymized report from {len(rows)} research events."
    report.finished_at = utcnow()
    db.commit()
    db.refresh(report)
    return report
