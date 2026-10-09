from __future__ import annotations

import pytest

from app.services.intent_compiler import (
    CompileOutcome,
    IntentCompiler,
    SemanticValidationError,
    extract_by_rules,
)
from app.services.llm.base import LLMFailure, LLMFailureKind
from app.services.llm.fake import FakeLLMClient

QUERY = "周六带8岁孩子去北京延庆徒步，8公里以内，爬升不超过300米，2小时左右，有水源补给，避开悬崖"

VALID_PAYLOAD = {
    "location": "北京延庆",
    "participants": {"children": True, "child_ages": [8]},
    "max_distance_km": 8,
    "max_elevation_gain_m": 300,
    "avoid_risk_types": ["risk_cliff_exposure"],
    "max_duration_minutes": 120,
    "required_facilities": ["water_source"],
}


class TestLadder:
    async def test_primary_success(self) -> None:
        compiler = IntentCompiler(FakeLLMClient([dict(VALID_PAYLOAD)]), primary_model="fake-small")
        outcome = await compiler.compile(QUERY)
        assert outcome.source == "llm_primary"
        assert outcome.intent is not None
        assert outcome.intent.max_distance_km == 8
        assert outcome.intent.avoid_risk_types[0].value == "risk_cliff_exposure"

    async def test_primary_invalid_falls_to_fallback_model(self) -> None:
        compiler = IntentCompiler(
            FakeLLMClient(["这不是 JSON", dict(VALID_PAYLOAD)]),
            primary_model="small",
            fallback_model="big",
        )
        outcome = await compiler.compile(QUERY)
        assert outcome.source == "llm_fallback"
        assert outcome.intent is not None

    async def test_all_llm_fail_falls_to_rules(self) -> None:
        compiler = IntentCompiler(
            FakeLLMClient([LLMFailure(LLMFailureKind.AUTH, "bad key")]),
            primary_model="small",
            fallback_model="big",
        )
        outcome = await compiler.compile(QUERY)
        assert outcome.source == "rule_extraction"
        assert outcome.intent is not None
        assert outcome.intent.max_distance_km == 8.0
        assert outcome.intent.max_elevation_gain_m == 300.0

    async def test_null_fields_stripped(self) -> None:
        payload = dict(VALID_PAYLOAD)
        payload["relaxation"] = None
        payload["min_rating"] = None
        compiler = IntentCompiler(FakeLLMClient([payload]), primary_model="m")
        outcome = await compiler.compile(QUERY)
        assert outcome.source == "llm_primary"
        assert outcome.intent is not None
        assert outcome.intent.relaxation == {}

    async def test_shell_unwrapped(self) -> None:
        compiler = IntentCompiler(
            FakeLLMClient([{"intent": dict(VALID_PAYLOAD)}]), primary_model="m"
        )
        outcome = await compiler.compile(QUERY)
        assert outcome.source == "llm_primary"
        assert outcome.intent is not None


class TestSemanticCheck:
    async def test_relaxation_without_consent_rejected(self) -> None:
        payload = dict(VALID_PAYLOAD)
        payload["relaxation"] = {
            "max_distance_km": {"authorized": True, "max_value": 10, "order": 0}
        }
        compiler = IntentCompiler(FakeLLMClient([payload]), primary_model="m")
        # 查询中没有放宽语言 → 语义校验拒绝 → 规则通道。
        outcome = await compiler.compile(QUERY)
        assert outcome.source == "rule_extraction"

    async def test_relaxation_with_consent_accepted(self) -> None:
        payload = dict(VALID_PAYLOAD)
        payload["relaxation"] = {
            "max_distance_km": {"authorized": True, "max_value": 10, "order": 0}
        }
        compiler = IntentCompiler(FakeLLMClient([payload]), primary_model="m")
        query = QUERY + "，没有合适的可以放宽到10公里"
        outcome = await compiler.compile(query)
        assert outcome.source == "llm_primary"
        assert outcome.intent is not None
        assert outcome.intent.relaxation["max_distance_km"].authorized is True

    async def test_risk_relaxation_rejected_by_domain(self) -> None:
        payload = dict(VALID_PAYLOAD)
        payload["relaxation"] = {
            "avoid_risk_types": {"authorized": True, "max_value": 1, "order": 0}
        }
        # 语义层先拒（字段不可放宽），域模型也会拒。
        from app.services.intent_compiler import _semantic_check

        with pytest.raises(SemanticValidationError):
            _semantic_check(payload, QUERY + " 可以放宽")


class TestRuleExtraction:
    def test_full_query(self) -> None:
        intent = extract_by_rules(QUERY)
        assert intent.location == "北京"
        assert intent.max_distance_km == 8.0
        assert intent.max_elevation_gain_m == 300.0
        assert intent.max_duration_minutes == 120
        assert intent.avoid_risk_types[0].value == "risk_cliff_exposure"
        assert "water_source" in [f.value for f in intent.required_facilities]
        assert intent.participants.children is True
        assert intent.participants.child_ages == [8]

    def test_no_location(self) -> None:
        intent = extract_by_rules("8公里以内的徒步路线")
        assert intent.location == ""
        assert intent.max_distance_km == 8.0

    def test_empty_query(self) -> None:
        intent = extract_by_rules("随便推荐一条路线")
        assert intent.required_fact_fields() == set()


class TestCompileOutcomeDataclass:
    def test_default_source_values(self) -> None:
        outcome = CompileOutcome(intent=None, source="failed")
        assert outcome.detail == ""
