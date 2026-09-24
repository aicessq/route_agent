from __future__ import annotations

from datetime import UTC, datetime

from app.domain.evidence import EvidenceRecord, FactStatus, FactType
from app.domain.report import (
    CandidateReport,
    CheckStatus,
    ConstraintCheck,
    DecisionReport,
    DecisionStatus,
    ExecutionMode,
)


def _evidence(**overrides) -> EvidenceRecord:
    payload = {
        "evidence_id": "ev-1",
        "route_id": "route-1",
        "fact_type": FactType.DISTANCE,
        "status": FactStatus.OK,
        "raw_value": 7.6,
        "normalized_value": 7.6,
        "unit": "km",
        "source": "fixture",
        "observed_at": datetime(2026, 9, 25, 8, 0, tzinfo=UTC),
        "valid_until": datetime(2026, 9, 25, 20, 0, tzinfo=UTC),
        "entity_revision": "r1",
    }
    payload.update(overrides)
    return EvidenceRecord.model_validate(payload)


class TestEvidenceRecord:
    def test_is_expired_boundary(self) -> None:
        record = _evidence()
        assert record.is_expired(datetime(2026, 9, 25, 19, 59, tzinfo=UTC)) is False
        assert record.is_expired(datetime(2026, 9, 25, 20, 0, tzinfo=UTC)) is True

    def test_with_expiry_status_flips_to_stale(self) -> None:
        record = _evidence()
        late = datetime(2026, 9, 25, 21, 0, tzinfo=UTC)
        assert record.with_expiry_status(late).status == FactStatus.STALE
        early = datetime(2026, 9, 25, 9, 0, tzinfo=UTC)
        assert record.with_expiry_status(early).status == FactStatus.OK

    def test_no_valid_until_never_expires(self) -> None:
        record = _evidence(valid_until=None)
        assert record.is_expired() is False

    def test_citation_id_stable_for_same_fields(self) -> None:
        assert _evidence().citation_id() == _evidence().citation_id()
        assert _evidence(entity_revision="r2").citation_id() != _evidence().citation_id()


def _candidate_report(
    route_id: str,
    *,
    hard_pass: bool,
    hard_statuses: list[CheckStatus],
) -> CandidateReport:
    checks = [
        ConstraintCheck(
            constraint="max_distance_km",
            strength="hard",
            status=status,
            expected=8,
            actual=7.6,
            fact_type=FactType.DISTANCE,
            reason="",
        )
        for status in hard_statuses
    ]
    return CandidateReport(
        route_id=route_id,
        title=route_id,
        source_kind="curated_route",
        checks=checks,
        hard_pass=hard_pass,
        soft_score=1.0,
    )


class TestDecisionReport:
    def test_status_verified_when_any_hard_pass(self) -> None:
        reports = [
            _candidate_report("a", hard_pass=True, hard_statuses=[CheckStatus.PASS]),
            _candidate_report("b", hard_pass=False, hard_statuses=[CheckStatus.FAIL]),
        ]
        assert DecisionReport.status_from_candidates(reports) == DecisionStatus.VERIFIED_MATCHES

    def test_status_insufficient_when_hard_unknown(self) -> None:
        reports = [
            _candidate_report("a", hard_pass=False, hard_statuses=[CheckStatus.UNKNOWN]),
            _candidate_report("b", hard_pass=False, hard_statuses=[CheckStatus.FAIL]),
        ]
        assert (
            DecisionReport.status_from_candidates(reports) == DecisionStatus.INSUFFICIENT_EVIDENCE
        )

    def test_status_no_match_when_all_fail(self) -> None:
        reports = [_candidate_report("b", hard_pass=False, hard_statuses=[CheckStatus.FAIL])]
        assert DecisionReport.status_from_candidates(reports) == DecisionStatus.NO_MATCH

    def test_serialization_round_trip(self) -> None:
        report = DecisionReport(
            status=DecisionStatus.VERIFIED_MATCHES,
            execution_mode=ExecutionMode.DETERMINISTIC_PREVIEW,
            intent_fingerprint="f" * 64,
            policy_version="fact-policy.v1",
            candidates=[_candidate_report("a", hard_pass=True, hard_statuses=[CheckStatus.PASS])],
            verified_route_ids=["a"],
        )
        restored = DecisionReport.model_validate(report.model_dump(mode="json"))
        assert restored == report
