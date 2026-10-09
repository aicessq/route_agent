from __future__ import annotations

import asyncio

from app.providers.fake import FakeRouteProvider
from app.runtime.local_store import InMemoryDecisionStore
from app.runtime.state import RunStatus
from app.services.decision_orchestrator import DecisionOrchestrator
from app.services.decision_service import DecisionService
from app.services.llm.fake import FakeLLMClient

FAMILY_QUERY = "周六带8岁孩子去北京延庆徒步，8公里以内，爬升不超过300米，避开悬崖"

VALID_INTENT_PAYLOAD = {
    "location": "北京·延庆",
    "participants": {"children": True, "child_ages": [8]},
    "max_distance_km": 8,
    "max_elevation_gain_m": 300,
    "avoid_risk_types": ["risk_cliff_exposure"],
}


def _orchestrator(
    llm_outputs: list | None = None,
    *,
    provider: FakeRouteProvider | None = None,
    max_repair_rounds: int = 2,
) -> DecisionOrchestrator:
    provider = provider or FakeRouteProvider()
    service = DecisionService(provider, provider)
    client = (
        FakeLLMClient(llm_outputs or [], model="fake-small") if llm_outputs is not None else None
    )
    return DecisionOrchestrator(
        service,
        llm_client=client,
        primary_model="fake-small" if client is not None else None,
        max_repair_rounds=max_repair_rounds,
    )


def _item(query: str | None = None, intent=None, candidate_limit: int = 10):
    from app.runtime.local_store import LocalDecision

    return LocalDecision(
        decision_id="test-decision",
        request_fingerprint="f",
        query=query,
        intent=intent,
        candidate_limit=candidate_limit,
        user_key="tester",
    )


class TestCompileAndDecide:
    async def test_query_compiled_by_llm(self) -> None:
        orchestrator = _orchestrator([dict(VALID_INTENT_PAYLOAD)])
        item = _item(query=FAMILY_QUERY)
        await orchestrator.run(item)
        assert item.status == RunStatus.SUCCEEDED
        assert item.compile_source == "llm_primary"
        assert item.report is not None
        assert item.report.verified_route_ids == ["bj-yanqing-001"]

    async def test_llm_failure_falls_to_rules(self) -> None:
        from app.services.llm.base import LLMFailure, LLMFailureKind

        orchestrator = _orchestrator([LLMFailure(LLMFailureKind.AUTH, "bad key")])
        item = _item(query=FAMILY_QUERY)
        await orchestrator.run(item)
        assert item.status == RunStatus.SUCCEEDED
        assert item.compile_source == "rule_extraction"
        assert item.report is not None
        assert item.report.verified_route_ids == ["bj-yanqing-001"]

    async def test_structured_intent_skips_compile(self) -> None:
        from app.domain.intent import RouteIntent

        intent = RouteIntent.model_validate(VALID_INTENT_PAYLOAD)
        orchestrator = _orchestrator([])
        item = _item(intent=intent)
        await orchestrator.run(item)
        assert item.status == RunStatus.SUCCEEDED
        assert item.compile_source == ""


class TestGateAndAnswers:
    async def test_missing_location_waits_for_user(self) -> None:
        payload = dict(VALID_INTENT_PAYLOAD)
        payload["location"] = ""
        orchestrator = _orchestrator([payload])
        item = _item(query="带8岁孩子徒步，8公里以内")
        await orchestrator.run(item)
        assert item.status == RunStatus.WAITING_USER
        assert item.question is not None
        assert item.question["field"] == "location"

    async def test_answers_resume_run(self) -> None:
        payload = dict(VALID_INTENT_PAYLOAD)
        payload["location"] = ""
        provider = FakeRouteProvider()
        service = DecisionService(provider, provider)
        llm = FakeLLMClient([payload], model="fake-small")
        orchestrator = DecisionOrchestrator(service, llm_client=llm, primary_model="fake-small")
        store = InMemoryDecisionStore()

        item, _ = await store.create(
            user_key="u",
            idempotency_key="k",
            query="带8岁孩子徒步，8公里以内，避开悬崖",
            intent=None,
            candidate_limit=10,
            runner=lambda decision: orchestrator.run(decision),
        )
        await asyncio.gather(item.task)
        assert item.status == RunStatus.WAITING_USER

        resumed = await store.apply_answers(
            item.decision_id,
            answers={"location": "北京·延庆"},
            authorize_relaxation=None,
            runner=lambda decision: orchestrator.run(decision),
        )
        await asyncio.gather(resumed.task)
        assert resumed.status == RunStatus.SUCCEEDED
        assert resumed.intent_version == 2
        assert resumed.report is not None
        assert resumed.report.verified_route_ids == ["bj-yanqing-001"]


class TestRepairFlows:
    async def test_authorized_relaxation_recovers_no_match(self) -> None:
        from app.domain.intent import RouteIntent

        # 门头沟 8.5km：8km 内 FAIL；授权放宽到 10km 后应通过。
        intent = RouteIntent.model_validate(
            {
                "location": "北京·门头沟",
                "max_distance_km": 8,
                "max_elevation_gain_m": 300,
                "relaxation": {
                    "max_distance_km": {"authorized": True, "max_value": 10, "order": 0}
                },
            }
        )
        orchestrator = _orchestrator([])
        item = _item(intent=intent)
        await orchestrator.run(item)
        assert item.status == RunStatus.SUCCEEDED
        assert item.report is not None
        assert "bj-mentougou-003" in item.report.verified_route_ids
        assert item.relaxation_applied[0].field == "max_distance_km"
        assert item.relaxation_applied[0].from_value == 8.0
        assert item.relaxation_applied[0].to_value == 10.0
        assert item.intent_version == 2
        assert item.repair_log[0]["action"] == "RELAX_AUTHORIZED_PREFERENCE"

    async def test_unauthorized_relaxation_never_applied(self) -> None:
        from app.domain.intent import RouteIntent

        intent = RouteIntent.model_validate(
            {"location": "北京·门头沟", "max_distance_km": 8, "max_elevation_gain_m": 300}
        )
        orchestrator = _orchestrator([])
        item = _item(intent=intent)
        await orchestrator.run(item)
        # 8.5km FAIL 且未授权 → 不放宽。
        assert item.status == RunStatus.NO_MATCH
        assert item.relaxation_applied == []

    async def test_no_progress_fetch_stops_early(self) -> None:
        from app.domain.intent import RouteIntent

        # 密云风险证据缺失 → FETCH 重取证无进展 → 进展门停止。
        intent = RouteIntent.model_validate(
            {
                "location": "北京·密云",
                "max_distance_km": 10,
                "max_elevation_gain_m": 300,
                "avoid_risk_types": ["risk_cliff_exposure"],
            }
        )
        orchestrator = _orchestrator([])
        item = _item(intent=intent)
        await orchestrator.run(item)
        assert item.status == RunStatus.INSUFFICIENT_EVIDENCE
        assert item.repair_log[0]["action"] == "FETCH_MISSING_EVIDENCE"
        assert item.repair_log[0]["progress"] == "none"
        assert len(item.repair_log) == 1  # 进展门阻止了第二轮空转

    async def test_replace_candidate_grows_recall(self) -> None:
        from app.domain.intent import RouteIntent

        intent = RouteIntent.model_validate({"location": "北京", "max_distance_km": 3})
        orchestrator = _orchestrator([])
        item = _item(intent=intent, candidate_limit=1)
        await orchestrator.run(item)
        assert item.status == RunStatus.NO_MATCH
        assert item.repair_rounds == 2
        assert all(entry["action"] == "REPLACE_CANDIDATE" for entry in item.repair_log)
        assert item.report is not None
        assert len(item.report.candidates) > 1

    async def test_repair_rounds_bounded(self) -> None:
        from app.domain.intent import RouteIntent

        intent = RouteIntent.model_validate({"location": "北京", "max_distance_km": 1})
        orchestrator = _orchestrator([], max_repair_rounds=2)
        item = _item(intent=intent, candidate_limit=2)
        await orchestrator.run(item)
        assert item.repair_rounds <= 2

    async def test_cancel_requested_before_finalize(self) -> None:
        from app.domain.intent import RouteIntent

        intent = RouteIntent.model_validate(VALID_INTENT_PAYLOAD)
        orchestrator = _orchestrator([])
        item = _item(intent=intent)
        item.status = RunStatus.CANCEL_REQUESTED
        await orchestrator.run(item)
        assert item.status == RunStatus.CANCELLED
        assert item.report is None


class TestCostWiring:
    async def test_cost_report_attached_to_budget(self) -> None:
        orchestrator = _orchestrator([dict(VALID_INTENT_PAYLOAD)])
        item = _item(query=FAMILY_QUERY)
        await orchestrator.run(item)
        assert item.report is not None
        llm_budget = item.report.budget.get("llm")
        assert llm_budget is not None
        assert llm_budget["call_count"] >= 1
        assert llm_budget["total_tokens"] > 0
        assert llm_budget["calls"][0]["purpose"] == "intent_compile_primary"
