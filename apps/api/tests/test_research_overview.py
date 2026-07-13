from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.core.config import get_settings
from app.core.time import utcnow
from app.db.models import (
    AgentMessage,
    Artifact,
    BumpaConnection,
    Conversation,
    HermesProfile,
    ResearchEvent,
    ResearchReport,
    Tenant,
    User,
)
from app.db.session import SessionLocal
from app.providers.redaction import pseudonymize
from tests.conftest import auth_headers


def test_research_overview_covers_full_consent_safe_measurement_catalogue(
    client: TestClient,
) -> None:
    now = utcnow()
    created_ids: dict[type[object], list[str]] = {
        AgentMessage: [],
        Artifact: [],
        BumpaConnection: [],
        Conversation: [],
        HermesProfile: [],
        ResearchEvent: [],
        ResearchReport: [],
        Tenant: [],
        User: [],
    }
    with SessionLocal() as db:
        consented = Tenant(
            slug="overview-consented",
            name="Overview Consented",
            research_consent_status="granted",
        )
        withdrawn = Tenant(
            slug="overview-withdrawn",
            name="Overview Withdrawn",
            research_consent_status="withdrawn",
        )
        owner = User(name="Overview Owner", primary_phone_e164="+2348111000101")
        excluded_owner = User(name="Excluded Owner", primary_phone_e164="+2348111000102")
        db.add_all([consented, withdrawn, owner, excluded_owner])
        db.flush()

        conversation = Conversation(
            tenant_id=consented.id,
            user_id=owner.id,
            channel="web",
        )
        profile = HermesProfile(
            tenant_id=consented.id,
            profile_name=f"overview_{consented.id[:8]}",
            profile_path=f"/profiles/overview_{consented.id[:8]}",
            provider="hermes",
            api_internal_url="http://hermes:8898/v1",
            api_port=8898,
            encrypted_api_key="test-encrypted-key",
        )
        connection = BumpaConnection(
            tenant_id=consented.id,
            encrypted_api_key="test-encrypted-key",
            scope_type="business_id",
            scope_id="overview-business",
            provider="bumpa",
            last_successful_sync_at=now - timedelta(hours=1),
        )
        report = ResearchReport(
            report_type="weekly_memo",
            generated_by=owner.id,
            filters={},
            status="success",
            title="Overview report",
            finished_at=now,
        )
        db.add_all([conversation, profile, connection, report])
        db.flush()

        messages = [
            AgentMessage(
                tenant_id=consented.id,
                user_id=owner.id,
                hermes_profile_id=profile.id,
                conversation_id=conversation.id,
                channel="web",
                direction="outbound",
                content="Safe answer",
                redacted_content="Safe answer",
                latency_ms=latency,
                created_at=now - timedelta(minutes=index),
            )
            for index, latency in enumerate((100, 300), start=1)
        ]
        events = [
            ResearchEvent(
                tenant_id=consented.id,
                user_id=owner.id,
                conversation_id=conversation.id,
                channel="web",
                event_type="user_message_received",
                redacted_text="Which item sold best today?",
                primary_intent="sales_analysis",
                business_function="sales",
                ai_help_type="data_lookup",
                complexity="simple_lookup",
                bumpa_data_used="products",
                created_at=created_at,
            )
            for created_at in (now - timedelta(days=40), now - timedelta(hours=2))
        ]
        events.append(
            ResearchEvent(
                tenant_id=consented.id,
                user_id=owner.id,
                conversation_id=conversation.id,
                channel="whatsapp",
                event_type="user_message_received",
                redacted_text="What should I restock next?",
                primary_intent="inventory_management",
                business_function="stock",
                ai_help_type="recommendation",
                complexity="single_step_reasoning",
                bumpa_data_used="mixed",
                created_at=now - timedelta(minutes=10),
            )
        )
        events.append(
            ResearchEvent(
                tenant_id=consented.id,
                user_id=owner.id,
                conversation_id=conversation.id,
                channel="whatsapp",
                event_type="assistant_response_sent",
                redacted_text="ASSISTANT-EVENT-NOT-A-QUESTION",
                primary_intent="assistant_only",
                business_function="admin",
                ai_help_type="explanation",
                complexity="simple_lookup",
                bumpa_data_used="none",
                created_at=now - timedelta(minutes=9),
            )
        )
        excluded = ResearchEvent(
            tenant_id=withdrawn.id,
            user_id=excluded_owner.id,
            channel="web",
            event_type="user_message_received",
            redacted_text="WITHDRAWN-ONLY-QUESTION",
            primary_intent="withdrawn_only",
            business_function="admin",
            ai_help_type="troubleshooting",
            complexity="strategic_reasoning",
            bumpa_data_used="customers",
            created_at=now,
        )
        artifact = Artifact(
            report_id=report.id,
            format="pdf",
            storage_key=f"{report.id}/report.pdf",
            content_type="application/pdf",
            byte_size=128,
            checksum_sha256="a" * 64,
        )
        db.add_all([*messages, *events, excluded, artifact])
        db.commit()

        for model, rows in (
            (Tenant, [consented, withdrawn]),
            (User, [owner, excluded_owner]),
            (Conversation, [conversation]),
            (HermesProfile, [profile]),
            (BumpaConnection, [connection]),
            (ResearchReport, [report]),
            (AgentMessage, messages),
            (ResearchEvent, [*events, excluded]),
            (Artifact, [artifact]),
        ):
            created_ids[model] = [row.id for row in rows]
        consented_id = consented.id

    researcher = auth_headers(client, "+2348099990002")
    try:
        response = client.get("/v1/research/overview", headers=researcher)
        assert response.status_code == 200, response.text
        payload = response.json()

        assert payload["active_smes"]["day"] >= 1
        assert payload["active_smes"]["week"] >= 1
        assert payload["active_smes"]["month"] >= 1
        assert payload["active_users_by_channel"]["web"] >= 1
        assert payload["messages_by_channel"]["whatsapp"] >= 1
        assert payload["questions_by_category"]["sales_analysis"] >= 2
        assert payload["questions_by_intent"] == payload["questions_by_category"]
        assert payload["questions_by_business_function"]["stock"] >= 1
        assert payload["questions_by_complexity"]["simple_lookup"] >= 2
        assert payload["questions_by_ai_help_type"]["recommendation"] >= 1
        assert payload["bumpa_data_usage"]["mixed"] >= 1
        assert payload["hermes_response_latency"] == {
            "samples": 2,
            "average_ms": 200,
            "p50_ms": 100,
            "p95_ms": 300,
        }
        assert payload["bumpa_sync_freshness"]["fresh_24h"] >= 1
        assert payload["report_generation"]["by_type"]["weekly_memo"] >= 1
        assert payload["exports"]["by_format"]["pdf"] >= 1
        assert any(row["retained_30d"] >= 1 for row in payload["retention_by_cohort"])
        expected_pseudonym = pseudonymize(
            consented_id,
            get_settings().research_pseudonym_key,
            namespace="tenant",
        )
        repeat = payload["repeat_usage"]
        assert repeat["repeat_smes"] >= 1
        assert any(row["tenant_pseudonym"] == expected_pseudonym for row in repeat["by_sme"])
        assert any(
            row == {"label": "Which item sold best today?", "count": 2}
            for row in payload["most_common_sales_questions"]
        )
        assert payload["most_common_inventory_questions"]
        assert payload["most_common_advice_requests"]
        assert payload["top_recurring_problems"]

        serialized = response.text
        assert consented_id not in serialized
        assert "WITHDRAWN-ONLY-QUESTION" not in serialized
        assert "withdrawn_only" not in serialized
        assert "ASSISTANT-EVENT-NOT-A-QUESTION" not in serialized
        assert "assistant_only" not in payload["questions_by_category"]
    finally:
        with SessionLocal() as db:
            for model in (
                Artifact,
                AgentMessage,
                ResearchEvent,
                ResearchReport,
                BumpaConnection,
                HermesProfile,
                Conversation,
                User,
                Tenant,
            ):
                ids = created_ids[model]
                if ids:
                    db.execute(delete(model).where(model.id.in_(ids)))
            db.commit()
