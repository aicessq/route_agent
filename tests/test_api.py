from __future__ import annotations

import time

from fastapi.testclient import TestClient


class TestHealth:
    def test_health_ok(self, client: TestClient) -> None:
        response = client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["code"] == 0
        assert body["data"]["status"] == "ok"


class TestPreview:
    def test_preview_returns_verified_report(
        self, client: TestClient, family_intent_payload: dict
    ) -> None:
        response = client.post("/api/v1/decisions/preview", json={"intent": family_intent_payload})
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["status"] == "VERIFIED_MATCHES"
        assert data["verified_route_ids"] == ["bj-yanqing-001"]
        checks = data["candidates"][0]["checks"]
        assert all(check["status"] == "PASS" for check in checks if check["strength"] == "hard")

    def test_preview_rejects_invalid_intent(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/decisions/preview",
            json={"intent": {"location": ""}},
        )
        assert response.status_code == 422


class TestCreateAndPoll:
    def test_create_returns_202_and_polls_to_success(
        self, client: TestClient, family_intent_payload: dict
    ) -> None:
        response = client.post(
            "/api/v1/decisions",
            json={"intent": family_intent_payload},
            headers={"Idempotency-Key": "test-key-0001"},
        )
        assert response.status_code == 202
        decision_id = response.json()["data"]["decision_id"]
        assert decision_id

        final = None
        for _ in range(20):
            status_response = client.get(f"/api/v1/decisions/{decision_id}")
            assert status_response.status_code == 200
            final = status_response.json()["data"]
            if final["status"] in {"SUCCEEDED", "NO_MATCH", "INSUFFICIENT_EVIDENCE", "FAILED"}:
                break
            time.sleep(0.05)
        assert final is not None
        assert final["status"] == "SUCCEEDED"
        assert final["report"]["verified_route_ids"] == ["bj-yanqing-001"]

    def test_create_requires_idempotency_key(
        self, client: TestClient, family_intent_payload: dict
    ) -> None:
        response = client.post("/api/v1/decisions", json={"intent": family_intent_payload})
        assert response.status_code == 400
        assert response.json()["code"] == 10001

    def test_create_rejects_short_idempotency_key(
        self, client: TestClient, family_intent_payload: dict
    ) -> None:
        response = client.post(
            "/api/v1/decisions",
            json={"intent": family_intent_payload},
            headers={"Idempotency-Key": "short"},
        )
        assert response.status_code == 400

    def test_idempotent_replay_returns_same_decision(
        self, client: TestClient, family_intent_payload: dict
    ) -> None:
        headers = {"Idempotency-Key": "test-key-replay-0001"}
        first = client.post(
            "/api/v1/decisions", json={"intent": family_intent_payload}, headers=headers
        )
        second = client.post(
            "/api/v1/decisions", json={"intent": family_intent_payload}, headers=headers
        )
        assert first.status_code == 202
        assert second.status_code == 200
        assert first.json()["data"]["decision_id"] == second.json()["data"]["decision_id"]

    def test_idempotency_conflict_on_different_request(
        self, client: TestClient, family_intent_payload: dict
    ) -> None:
        headers = {"Idempotency-Key": "test-key-conflict-001"}
        first = client.post(
            "/api/v1/decisions", json={"intent": family_intent_payload}, headers=headers
        )
        assert first.status_code == 202
        modified = dict(family_intent_payload)
        modified["max_distance_km"] = 12
        conflict = client.post("/api/v1/decisions", json={"intent": modified}, headers=headers)
        assert conflict.status_code == 409
        assert conflict.json()["code"] == 10002

    def test_get_unknown_decision_returns_404(self, client: TestClient) -> None:
        response = client.get("/api/v1/decisions/nonexistent-id")
        assert response.status_code == 404
        assert response.json()["code"] == 10003


class TestSummary:
    def test_summary_after_completion(
        self, client: TestClient, family_intent_payload: dict
    ) -> None:
        decision_id = client.post(
            "/api/v1/decisions",
            json={"intent": family_intent_payload},
            headers={"Idempotency-Key": "test-key-summary-001"},
        ).json()["data"]["decision_id"]
        for _ in range(20):
            status = client.get(f"/api/v1/decisions/{decision_id}").json()["data"]
            if status["status"] == "SUCCEEDED":
                break
            time.sleep(0.05)
        response = client.post(f"/api/v1/decisions/{decision_id}/summary")
        assert response.status_code == 200
        data = response.json()["data"]
        assert "推荐" in data["summary"]
        assert "复核" in data["summary"]

    def test_summary_rejected_when_report_not_ready(self, slow_client: TestClient) -> None:
        decision_id = slow_client.post(
            "/api/v1/decisions",
            json={
                "intent": {
                    "location": "北京·延庆",
                    "max_distance_km": 8,
                    "max_elevation_gain_m": 300,
                }
            },
            headers={"Idempotency-Key": "test-key-summary-002"},
        ).json()["data"]["decision_id"]
        response = slow_client.post(f"/api/v1/decisions/{decision_id}/summary")
        assert response.status_code == 409


class TestCancel:
    def test_cancel_running_decision(self, slow_client: TestClient) -> None:
        decision_id = slow_client.post(
            "/api/v1/decisions",
            json={
                "intent": {
                    "location": "北京·延庆",
                    "max_distance_km": 8,
                    "max_elevation_gain_m": 300,
                }
            },
            headers={"Idempotency-Key": "test-key-cancel-0001"},
        ).json()["data"]["decision_id"]
        cancel_response = slow_client.delete(f"/api/v1/decisions/{decision_id}")
        assert cancel_response.status_code == 200
        assert cancel_response.json()["data"]["status"] in {"CANCEL_REQUESTED", "CANCELLED"}

        final = None
        for _ in range(30):
            final = slow_client.get(f"/api/v1/decisions/{decision_id}").json()["data"]
            if final["status"] in {"CANCELLED", "SUCCEEDED", "NO_MATCH", "FAILED"}:
                break
            time.sleep(0.1)
        assert final is not None
        assert final["status"] == "CANCELLED"
        assert final["report"] is None

    def test_cancel_unknown_decision_returns_404(self, client: TestClient) -> None:
        response = client.delete("/api/v1/decisions/nonexistent-id")
        assert response.status_code == 404
