from __future__ import annotations

from app.domain.intent import RouteIntent
from app.domain.report import CheckStatus, ConstraintCheck, DecisionReport, DecisionStatus
from app.services.llm.fake import FakeLLMClient
from app.services.repair import (
    RepairAction,
    RepairContext,
    RepairPlanner,
    RepairProposal,
    count_unknown_hard,
    deterministic_proposal,
    has_progress,
    soft_failure_fields,
    summarize_context,
    validate_proposal,
)


def _intent(**overrides) -> RouteIntent:
    payload = {"location": "北京", "max_distance_km": 8, "max_elevation_gain_m": 300}
    payload.update(overrides)
    return RouteIntent.model_validate(payload)


def _report(
    *,
    verified: list[str],
    unknown: int = 0,
    candidates: int = 4,
    soft_fail_fields: list[str] | None = None,
) -> DecisionReport:
    checks = [
        ConstraintCheck(
            constraint="max_distance_km", strength="hard", status=CheckStatus.PASS, reason=""
        )
    ]
    soft_checks = [
        ConstraintCheck(constraint=field, strength="soft", status=CheckStatus.FAIL, reason="")
        for field in (soft_fail_fields or [])
    ]
    return DecisionReport(
        status=DecisionStatus.VERIFIED_MATCHES if verified else DecisionStatus.NO_MATCH,
        execution_mode="local_async",
        intent_fingerprint="f" * 64,
        policy_version="p",
        verified_route_ids=verified,
        candidates=[
            {
                "route_id": f"route-{index}",
                "title": f"route-{index}",
                "source_kind": "public_track",
                "checks": checks + soft_checks,
                "hard_pass": (index == 0 and bool(verified)),
                "soft_score": 1.0,
            }
            for index in range(candidates)
        ],
    )


def _context(**overrides) -> RepairContext:
    defaults = dict(
        intent=_intent(),
        report=_report(verified=[]),
        unknown_hard_count=0,
        verified_count=0,
        candidate_count=4,
        candidate_limit=10,
        max_candidates=50,
        asked_question=False,
        question_rounds=0,
        soft_failure_fields=[],
    )
    defaults.update(overrides)
    return RepairContext(**defaults)


class TestValidation:
    def test_fetch_requires_unknown(self) -> None:
        proposal = RepairProposal(action=RepairAction.FETCH_MISSING_EVIDENCE)
        assert validate_proposal(proposal, _context(unknown_hard_count=2)) is None
        assert validate_proposal(proposal, _context(unknown_hard_count=0)) is not None

    def test_fetch_rejected_when_verified(self) -> None:
        proposal = RepairProposal(action=RepairAction.FETCH_MISSING_EVIDENCE)
        assert validate_proposal(proposal, _context(verified_count=1)) is not None

    def test_relax_requires_authorization(self) -> None:
        proposal = RepairProposal(
            action=RepairAction.RELAX_AUTHORIZED_PREFERENCE, field="max_distance_km"
        )
        assert validate_proposal(proposal, _context()) is not None
        authorized = _intent(
            relaxation={"max_distance_km": {"authorized": True, "max_value": 10, "order": 0}}
        )
        assert validate_proposal(proposal, _context(intent=authorized)) is None

    def test_replace_respects_candidate_budget(self) -> None:
        proposal = RepairProposal(action=RepairAction.REPLACE_CANDIDATE)
        assert validate_proposal(proposal, _context(candidate_count=4, max_candidates=50)) is None
        assert (
            validate_proposal(proposal, _context(candidate_count=50, max_candidates=50)) is not None
        )

    def test_ask_user_respects_question_rounds(self) -> None:
        proposal = RepairProposal(action=RepairAction.ASK_USER)
        assert validate_proposal(proposal, _context(question_rounds=0)) is None
        assert validate_proposal(proposal, _context(question_rounds=2)) is not None

    def test_stop_always_valid(self) -> None:
        proposal = RepairProposal(action=RepairAction.STOP_WITH_GAPS)
        assert validate_proposal(proposal, _context(verified_count=1)) is None


class TestDeterministicPolicy:
    def test_verified_stops(self) -> None:
        assert (
            deterministic_proposal(_context(verified_count=1)).action == RepairAction.STOP_WITH_GAPS
        )

    def test_unknown_fetches_first(self) -> None:
        proposal = deterministic_proposal(_context(unknown_hard_count=3))
        assert proposal.action == RepairAction.FETCH_MISSING_EVIDENCE

    def test_authorized_relaxation_before_ask(self) -> None:
        authorized = _intent(
            relaxation={"max_distance_km": {"authorized": True, "max_value": 10, "order": 0}}
        )
        proposal = deterministic_proposal(
            _context(intent=authorized, soft_failure_fields=["max_duration_minutes"])
        )
        assert proposal.action == RepairAction.RELAX_AUTHORIZED_PREFERENCE
        assert proposal.field == "max_distance_km"

    def test_soft_failure_asks_user(self) -> None:
        proposal = deterministic_proposal(_context(soft_failure_fields=["max_duration_minutes"]))
        assert proposal.action == RepairAction.ASK_USER
        assert proposal.field == "max_duration_minutes"

    def test_budget_left_replaces(self) -> None:
        proposal = deterministic_proposal(_context(candidate_count=4, max_candidates=50))
        assert proposal.action == RepairAction.REPLACE_CANDIDATE

    def test_exhausted_stops(self) -> None:
        proposal = deterministic_proposal(
            _context(candidate_count=50, max_candidates=50, question_rounds=2)
        )
        assert proposal.action == RepairAction.STOP_WITH_GAPS


class TestProgressGate:
    def test_verified_increase_is_progress(self) -> None:
        before = _report(verified=[])
        after = _report(verified=["pass"])
        assert has_progress(before, after) is True

    def test_verified_decrease_is_not_progress(self) -> None:
        before = _report(verified=["pass"])
        after = _report(verified=[])
        assert has_progress(before, after) is False

    def test_unknown_decrease_is_progress(self) -> None:
        before = DecisionReport(
            status=DecisionStatus.NO_MATCH,
            execution_mode="local_async",
            intent_fingerprint="f" * 64,
            policy_version="p",
            candidates=[
                {
                    "route_id": "r",
                    "title": "r",
                    "source_kind": "public_track",
                    "checks": [
                        ConstraintCheck(
                            constraint="avoid_risk_types",
                            strength="hard",
                            status=CheckStatus.UNKNOWN,
                            reason="",
                        )
                    ],
                    "hard_pass": False,
                    "soft_score": 0.0,
                }
            ],
        )
        after = _report(verified=[], candidates=1)
        assert count_unknown_hard(before) == 1
        assert count_unknown_hard(after) == 0
        assert has_progress(before, after) is True

    def test_no_change_is_not_progress(self) -> None:
        before = _report(verified=[], candidates=4)
        after = _report(verified=[], candidates=4)
        assert has_progress(before, after) is False


class TestSoftFailureFields:
    def test_collects_only_from_verified(self) -> None:
        report = _report(verified=["pass"], soft_fail_fields=["max_duration_minutes"])
        assert soft_failure_fields(report) == ["max_duration_minutes"]
        report_no_verified = _report(verified=[], soft_fail_fields=["max_duration_minutes"])
        assert soft_failure_fields(report_no_verified) == []


class TestLLMPlanner:
    async def test_valid_llm_proposal_accepted(self) -> None:
        planner = RepairPlanner(
            FakeLLMClient([{"action": "FETCH_MISSING_EVIDENCE", "reason": "缺证据"}]), model="m"
        )
        proposal, source = await planner.propose(_context(unknown_hard_count=2))
        assert source == "llm"
        assert proposal.action == RepairAction.FETCH_MISSING_EVIDENCE

    async def test_invalid_llm_action_falls_back(self) -> None:
        planner = RepairPlanner(
            FakeLLMClient([{"action": "HACK_THE_DB", "reason": "x"}]), model="m"
        )
        proposal, source = await planner.propose(_context(unknown_hard_count=2))
        assert source == "deterministic"
        assert proposal.action == RepairAction.FETCH_MISSING_EVIDENCE

    async def test_llm_unauthorized_relax_rejected(self) -> None:
        planner = RepairPlanner(
            FakeLLMClient([{"action": "RELAX_AUTHORIZED_PREFERENCE", "field": "max_distance_km"}]),
            model="m",
        )
        proposal, source = await planner.propose(_context())
        assert source == "deterministic"

    async def test_llm_failure_falls_back(self) -> None:
        from app.services.llm.base import LLMFailure, LLMFailureKind

        planner = RepairPlanner(FakeLLMClient([LLMFailure(LLMFailureKind.AUTH, "x")]), model="m")
        proposal, source = await planner.propose(_context(unknown_hard_count=2))
        assert source == "deterministic"


class TestSummarizeContext:
    def test_builds_from_report(self) -> None:
        report = _report(verified=[])
        context = summarize_context(_intent(), report, candidate_limit=5)
        assert context.candidate_count == 4
        assert context.verified_count == 0
