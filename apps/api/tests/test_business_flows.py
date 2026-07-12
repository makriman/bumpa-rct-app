from datetime import date, timedelta

from fastapi.testclient import TestClient

from tests.conftest import auth_headers


def test_sync_chat_research_report_flow(client: TestClient) -> None:
    owner = auth_headers(client, "+2348012345678")
    today = date.today()
    sync = client.post(
        "/v1/bumpa/sync",
        headers=owner,
        json={"date_from": str(today - timedelta(days=29)), "date_to": str(today)},
    )
    assert sync.status_code == 200, sync.text
    assert sync.json()["status"] == "success"
    assert len(sync.json()["dataset_results"]) == 10

    chat = client.post(
        "/v1/chat/web",
        headers=owner,
        json={"message": "What sold best and how are sales?", "client_message_id": "web-1"},
    )
    assert chat.status_code == 200, chat.text
    assert "Sales:" in chat.json()["answer"]
    duplicate = client.post(
        "/v1/chat/web",
        headers=owner,
        json={"message": "What sold best and how are sales?", "client_message_id": "web-1"},
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["inbound_message_id"] == chat.json()["inbound_message_id"]

    history = client.get("/v1/chat/conversations", headers=owner)
    assert history.status_code == 200 and history.json()
    detail = client.get(f"/v1/chat/conversations/{chat.json()['conversation_id']}", headers=owner)
    assert len(detail.json()["messages"]) == 2

    researcher = auth_headers(client, "+2348099990002")
    overview = client.get("/v1/research/overview", headers=researcher)
    assert overview.status_code == 200
    assert overview.json()["research_events"] >= 1
    report = client.post(
        "/v1/research/reports",
        headers=researcher,
        json={"report_type": "question_taxonomy", "formats": ["csv", "jsonl", "pdf"]},
    )
    assert report.status_code == 201, report.text
    assert report.json()["status"] == "success"
    report_id = report.json()["id"]
    metadata = client.get(f"/v1/research/reports/{report_id}", headers=researcher).json()
    assert {item["format"] for item in metadata["artifacts"]} == {"csv", "jsonl", "pdf"}
    pdf = client.get(f"/v1/research/reports/{report_id}/download/pdf", headers=researcher)
    assert pdf.status_code == 200
    assert pdf.content.startswith(b"%PDF-1.4")


def test_admin_can_onboard_tenant_and_all_mutations_are_audited(client: TestClient) -> None:
    operator = auth_headers(client, "+2348099990001")
    created = client.post(
        "/v1/admin/tenants",
        headers=operator,
        json={"slug": "new-shop", "name": "New Shop", "country": "NG"},
    )
    assert created.status_code == 201, created.text
    tenant_id = created.json()["id"]
    user = client.post(
        f"/v1/admin/tenants/{tenant_id}/users",
        headers=operator,
        json={"name": "New Owner", "phone_e164": "+2348123456789", "role": "owner"},
    )
    assert user.status_code == 201
    phone = client.post(
        f"/v1/admin/tenants/{tenant_id}/phones",
        headers=operator,
        json={"user_id": user.json()["user_id"], "phone_e164": "+2348123456789"},
    )
    assert phone.status_code == 201
    connection = client.post(
        f"/v1/admin/tenants/{tenant_id}/bumpa",
        headers=operator,
        json={
            "api_key": "local-key",
            "scope_type": "business_id",
            "scope_id": "new-shop-id",
            "provider": "local",
        },
    )
    assert connection.status_code == 200
    profile = client.post(f"/v1/admin/tenants/{tenant_id}/hermes-profile", headers=operator)
    assert profile.status_code == 200
    audits = client.get("/v1/admin/audit", headers=operator).json()
    assert any(row["action"] == "tenant.created" for row in audits)
    assert any(row["action"] == "tenant.bumpa_connection.saved" for row in audits)
