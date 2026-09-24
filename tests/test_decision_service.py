from __future__ import annotations

import pytest

from app.domain.intent import RouteIntent
from app.domain.report import DecisionStatus
from app.providers.fake import FakeRouteProvider
from app.services.decision_service import DecisionService


def _intent_payload(**overrides) -> dict:
    payload = {
        "location": "北京",
        "max_distance_km": 8,
        "max_elevation_gain_m": 300,
        "avoid_risk_types": ["risk_cliff_exposure"],
    }
    payload.update(overrides)
    return payload


class TestDecisionService:
    async def test_full_pipeline_verified_match(self, decision_service: DecisionService) -> None:
        intent = RouteIntent.model_validate(
            _intent_payload(
                location="北京·延庆",
                max_duration_minutes=120,
                required_facilities=["water_source"],
            )
        )
        report = await decision_service.decide(intent)
        assert report.status == DecisionStatus.VERIFIED_MATCHES
        assert report.verified_route_ids == ["bj-yanqing-001"]
        assert report.budget["candidate_count"] == 1
        assert report.budget["provider_calls"]["search"] == 1
        assert report.budget["provider_calls"]["evidence"] == 1

    async def test_unknown_risk_not_promoted_to_verified(
        self, decision_service: DecisionService
    ) -> None:
        # 北京全城：密云轨迹缺风险证据，不得进入已验证推荐。
        intent = RouteIntent.model_validate(_intent_payload(location="北京", max_distance_km=10))
        report = await decision_service.decide(intent)
        assert report.status == DecisionStatus.VERIFIED_MATCHES
        assert "bj-miyun-004" not in report.verified_route_ids
        miyun = next(c for c in report.candidates if c.route_id == "bj-miyun-004")
        assert miyun.hard_pass is False

    async def test_evidence_provider_failure_yields_insufficient_evidence(self) -> None:
        provider = FakeRouteProvider(fail_evidence=True)
        service = DecisionService(provider, provider)
        report = await service.decide(RouteIntent.model_validate(_intent_payload()))
        assert report.status == DecisionStatus.INSUFFICIENT_EVIDENCE
        assert any("evidence_provider_unavailable" in w for w in report.warnings)
        # 依赖故障不得伪造任何已验证路线。
        assert report.verified_route_ids == []

    async def test_search_provider_failure_raises(self) -> None:
        provider = FakeRouteProvider(fail_search=True)
        service = DecisionService(provider, provider)
        with pytest.raises(RuntimeError):
            await service.decide(RouteIntent.model_validate(_intent_payload()))

    async def test_no_candidates_is_business_no_match(
        self, decision_service: DecisionService
    ) -> None:
        intent = RouteIntent.model_validate({"location": "上海", "max_distance_km": 8})
        report = await decision_service.decide(intent)
        assert report.status == DecisionStatus.NO_MATCH
        assert "no_candidate_found" in report.warnings

    async def test_no_verifiable_constraints_not_verified(
        self, decision_service: DecisionService
    ) -> None:
        intent = RouteIntent.model_validate({"location": "北京·延庆"})
        report = await decision_service.decide(intent)
        assert report.status == DecisionStatus.INSUFFICIENT_EVIDENCE
        assert "request_has_no_verifiable_constraints" in report.warnings
        assert report.verified_route_ids == []

    async def test_authorized_relaxation_not_auto_applied(
        self, decision_service: DecisionService
    ) -> None:
        # 授权放宽只记录在 Intent 中；Verifier 仍按原阈值判定，放宽由 Repair 阶段显式执行。
        intent = RouteIntent.model_validate(
            _intent_payload(
                location="北京·门头沟",
                max_distance_km=8,
                relaxation={"max_distance_km": {"authorized": True, "max_value": 10, "order": 0}},
            )
        )
        assert intent.relaxation_for("max_distance_km").authorized is True
        report = await decision_service.decide(intent)
        mentougou = next(c for c in report.candidates if c.route_id == "bj-mentougou-003")
        distance = next(
            check for check in mentougou.checks if check.constraint == "max_distance_km"
        )
        assert distance.status == "FAIL"
        assert "bj-mentougou-003" not in report.verified_route_ids
