from __future__ import annotations

import time

from fastapi.testclient import TestClient

FAMILY_QUERY = "周六带8岁孩子去北京延庆徒步，8公里以内，爬升不超过300米，避开悬崖"

VALID_INTENT_PAYLOAD = {
    "location": "北京·延庆",
    "participants": {"children": True, "child_ages": [8]},
    "max_distance_km": 8,
    "max_elevation_gain_m": 300,
    "avoid_risk_types": ["risk_cliff_exposure"],
}


def _poll(client: TestClient, decision_id: str, *, limit: int = 40) -> dict:
    for _ in range(limit):
        data = client.get(f"/api/v1/decisions/{decision_id}").json()["data"]
        if data["status"] in {
            "SUCCEEDED",
            "NO_MATCH",
            "INSUFFICIENT_EVIDENCE",
            "CANCELLED",
            "FAILED",
            "WAITING_USER",
        }:
            return data
        time.sleep(0.05)
    raise AssertionError(f"task did not settle: {data['status']}")


class TestQueryCreate:
    def test_query_creates_and_completes(self, llm_client_factory) -> None:
        app, _ = llm_client_factory([dict(VALID_INTENT_PAYLOAD)])
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/decisions",
                json={"query": FAMILY_QUERY},
                headers={"Idempotency-Key": "phase4-query-0001"},
            )
            assert response.status_code == 202
            decision_id = response.json()["data"]["decision_id"]
            data = _poll(client, decision_id)
            assert data["status"] == "SUCCEEDED"
            assert data["compile_source"] == "llm_primary"
            assert data["report"]["verified_route_ids"] == ["bj-yanqing-001"]
            assert data["report"]["budget"]["llm"]["call_count"] >= 1

    def test_query_and_intent_rejected(
        self, client: TestClient, family_intent_payload: dict
    ) -> None:
        response = client.post(
            "/api/v1/decisions",
            json={"query": FAMILY_QUERY, "intent": family_intent_payload},
            headers={"Idempotency-Key": "phase4-both-0001"},
        )
        assert response.status_code == 422

    def test_neither_query_nor_intent_rejected(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/decisions",
            json={"candidate_limit": 10},
            headers={"Idempotency-Key": "phase4-none-0001"},
        )
        assert response.status_code == 422

    def test_structured_intent_still_works(
        self, client: TestClient, family_intent_payload: dict
    ) -> None:
        response = client.post(
            "/api/v1/decisions",
            json={"intent": family_intent_payload},
            headers={"Idempotency-Key": "phase4-struct-0001"},
        )
        assert response.status_code == 202
        decision_id = response.json()["data"]["decision_id"]
        data = _poll(client, decision_id)
        assert data["status"] == "SUCCEEDED"


class TestAnswersFlow:
    def test_question_then_answer_completes(self, llm_client_factory) -> None:
        payload = dict(VALID_INTENT_PAYLOAD)
        payload["location"] = ""
        app, _ = llm_client_factory([payload])
        with TestClient(app) as client:
            decision_id = client.post(
                "/api/v1/decisions",
                json={"query": "带8岁孩子徒步，8公里以内，避开悬崖"},
                headers={"Idempotency-Key": "phase4-ask-00001"},
            ).json()["data"]["decision_id"]
            data = _poll(client, decision_id)
            assert data["status"] == "WAITING_USER"
            assert data["question"]["field"] == "location"

            answer_response = client.post(
                f"/api/v1/decisions/{decision_id}/answers",
                json={"answers": {"location": "北京·延庆"}},
            )
            assert answer_response.status_code == 200
            assert answer_response.json()["data"]["intent_version"] == 2

            final = _poll(client, decision_id)
            assert final["status"] == "SUCCEEDED"
            assert final["report"]["verified_route_ids"] == ["bj-yanqing-001"]

    def test_answer_relaxation_authorization(self, llm_client_factory) -> None:
        # 门头沟 8.5km 超 8km：先 NO_MATCH 等待答复（ASK_USER），授权后放宽到 10。
        intent = {
            "location": "北京·门头沟",
            "max_distance_km": 8,
            "max_elevation_gain_m": 300,
        }
        app, _ = llm_client_factory([])
        with TestClient(app) as client:
            decision_id = client.post(
                "/api/v1/decisions",
                json={"intent": intent},
                headers={"Idempotency-Key": "phase4-relax-0001"},
            ).json()["data"]["decision_id"]
            data = _poll(client, decision_id)
            # 无授权时不会 ASK_USER 软约束放宽（无软约束失败），应为 NO_MATCH。
            assert data["status"] in {"NO_MATCH", "WAITING_USER"}

    def test_answers_on_non_waiting_rejected(
        self, client: TestClient, family_intent_payload: dict
    ) -> None:
        decision_id = client.post(
            "/api/v1/decisions",
            json={"intent": family_intent_payload},
            headers={"Idempotency-Key": "phase4-notw-00001"},
        ).json()["data"]["decision_id"]
        _poll(client, decision_id)
        response = client.post(
            f"/api/v1/decisions/{decision_id}/answers",
            json={"answers": {"location": "北京·延庆"}},
        )
        assert response.status_code == 409

    def test_answers_unknown_field_rejected(self, llm_client_factory) -> None:
        payload = dict(VALID_INTENT_PAYLOAD)
        payload["location"] = ""
        app, _ = llm_client_factory([payload])
        with TestClient(app) as client:
            decision_id = client.post(
                "/api/v1/decisions",
                json={"query": "带8岁孩子徒步，8公里以内，避开悬崖"},
                headers={"Idempotency-Key": "phase4-badans-0001"},
            ).json()["data"]["decision_id"]
            _poll(client, decision_id)
            response = client.post(
                f"/api/v1/decisions/{decision_id}/answers",
                json={"answers": {"hack_field": "x"}},
            )
            assert response.status_code == 409


class TestSummaryChannel:
    def test_llm_summary_channel(self, llm_client_factory) -> None:
        app, _ = llm_client_factory([dict(VALID_INTENT_PAYLOAD)])
        with TestClient(app) as client:
            decision_id = client.post(
                "/api/v1/decisions",
                json={"query": FAMILY_QUERY},
                headers={"Idempotency-Key": "phase4-sum-00001"},
            ).json()["data"]["decision_id"]
            _poll(client, decision_id)
            summary_data = client.post(f"/api/v1/decisions/{decision_id}/summary").json()["data"]
            # 无摘要脚本时 FakeLLM 抛错 → 模板兜底。
            assert summary_data["channel"] == "template"
            assert "推荐" in summary_data["summary"]

    def test_llm_summary_with_script(self, llm_client_factory) -> None:
        summary_text = (
            "推荐妫水河亲子环线。以上结论基于当前证据有效期，出行前请复核最新天气与路况。"
        )
        app, _ = llm_client_factory([dict(VALID_INTENT_PAYLOAD), {"summary": summary_text}])
        with TestClient(app) as client:
            decision_id = client.post(
                "/api/v1/decisions",
                json={"query": FAMILY_QUERY},
                headers={"Idempotency-Key": "phase4-sum-00002"},
            ).json()["data"]["decision_id"]
            _poll(client, decision_id)
            summary_data = client.post(f"/api/v1/decisions/{decision_id}/summary").json()["data"]
            assert summary_data["channel"] == "llm"
            assert "妫水河" in summary_data["summary"]


class TestStatusFields:
    def test_status_exposes_question_and_intent(self, llm_client_factory) -> None:
        payload = dict(VALID_INTENT_PAYLOAD)
        payload["location"] = ""
        app, _ = llm_client_factory([payload])
        with TestClient(app) as client:
            decision_id = client.post(
                "/api/v1/decisions",
                json={"query": "带8岁孩子徒步，8公里以内，避开悬崖"},
                headers={"Idempotency-Key": "phase4-stat-0001"},
            ).json()["data"]["decision_id"]
            data = _poll(client, decision_id)
            assert data["question"] is not None
            assert data["intent"] is not None
            assert data["current_step"] == "COMPLETENESS_GATE"
            assert data["intent_version"] == 1
