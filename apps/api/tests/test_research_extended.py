from fastapi.testclient import TestClient

from tests.conftest import auth_headers


def test_research_filters_taxonomy_lists_exports_and_missing_artifacts(client: TestClient) -> None:
    owner = auth_headers(client, "+2348012345678")
    client.post("/v1/chat/web", headers=owner, json={"message": "Who is my best customer?"})
    researcher = auth_headers(client, "+2348099990002")
    taxonomy = client.get("/v1/research/taxonomy", headers=researcher)
    assert "sales_analysis" in taxonomy.json()["primary_intent"]
    events = client.get(
        "/v1/research/events",
        headers=researcher,
        params={"channel": "web", "primary_intent": "customer_management"},
    )
    assert events.status_code == 200 and events.json()
    assert client.get("/v1/research/questions", headers=researcher).status_code == 200
    exported = client.post(
        "/v1/research/exports",
        headers=researcher,
        json={"report_type": "sme_usage", "formats": ["csv"]},
    )
    assert exported.status_code == 200
    report_id = exported.json()["id"]
    assert any(
        row["id"] == report_id
        for row in client.get("/v1/research/reports", headers=researcher).json()
    )
    assert (
        client.get(f"/v1/research/reports/{report_id}/download/csv", headers=researcher).status_code
        == 200
    )
    assert (
        client.get(f"/v1/research/reports/{report_id}/download/pdf", headers=researcher).status_code
        == 404
    )
    assert client.get("/v1/research/reports/not-found", headers=researcher).status_code == 404
