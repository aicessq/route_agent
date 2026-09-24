from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.domain.evidence import EvidenceRecord, FactStatus, FactType
from app.domain.intent import RouteIntent
from app.domain.verifier import RouteVerifier
from app.providers.fake import default_candidates, default_evidence


def _intent(**overrides) -> RouteIntent:
    payload = {
        "location": "北京",
        "max_distance_km": 8,
        "max_elevation_gain_m": 300,
        "avoid_risk_types": ["risk_cliff_exposure"],
    }
    payload.update(overrides)
    return RouteIntent.model_validate(payload)


def _candidates() -> dict[str, object]:
    return {candidate.route_id: candidate for candidate in default_candidates()}


def _evidence() -> dict[str, list[EvidenceRecord]]:
    by_route: dict[str, list[EvidenceRecord]] = {}
    for record in default_evidence(default_candidates()).values():
        by_route.setdefault(record.route_id, []).append(record)
    return by_route


class TestHardConstraints:
    def setup_method(self) -> None:
        self.verifier = RouteVerifier()
        self.candidates = _candidates()
        self.evidence = _evidence()

    def test_passing_candidate_all_hard_pass(self) -> None:
        report = self.verifier.verify(
            _intent(), self.candidates["bj-yanqing-001"], self.evidence["bj-yanqing-001"]
        )
        assert report.hard_pass is True
        hard = [check for check in report.checks if check.strength == "hard"]
        assert all(check.status == "PASS" for check in hard)

    def test_cliff_exposure_fails_hard_constraint(self) -> None:
        report = self.verifier.verify(
            _intent(), self.candidates["bj-huairou-002"], self.evidence["bj-huairou-002"]
        )
        assert report.hard_pass is False
        risk = next(check for check in report.checks if check.constraint == "avoid_risk_types")
        assert risk.status == "FAIL"

    def test_distance_over_limit_fails(self) -> None:
        report = self.verifier.verify(
            _intent(), self.candidates["bj-mentougou-003"], self.evidence["bj-mentougou-003"]
        )
        assert report.hard_pass is False
        distance = next(check for check in report.checks if check.constraint == "max_distance_km")
        assert distance.status == "FAIL"

    def test_missing_risk_evidence_is_unknown_not_pass(self) -> None:
        report = self.verifier.verify(
            _intent(), self.candidates["bj-miyun-004"], self.evidence["bj-miyun-004"]
        )
        assert report.hard_pass is False
        risk = next(check for check in report.checks if check.constraint == "avoid_risk_types")
        assert risk.status == "UNKNOWN"

    def test_missing_evidence_for_hard_numeric_is_unknown(self) -> None:
        report = self.verifier.verify(_intent(), self.candidates["bj-yanqing-001"], [])
        assert report.hard_pass is False
        assert all(check.status == "UNKNOWN" for check in report.checks if check.strength == "hard")


class TestFreshness:
    def setup_method(self) -> None:
        self.verifier = RouteVerifier()
        self.candidates = _candidates()

    def _record(
        self,
        fact_type: FactType,
        value: object,
        *,
        valid_until: datetime | None,
        status: FactStatus = FactStatus.OK,
    ) -> EvidenceRecord:
        return EvidenceRecord(
            evidence_id=f"test:{fact_type.value}",
            route_id="bj-yanqing-001",
            fact_type=fact_type,
            status=status,
            raw_value=value,
            normalized_value=value,
            source="fixture",
            observed_at=datetime.now(UTC) - timedelta(hours=6),
            valid_until=valid_until,
        )

    def test_stale_static_fact_within_grace_is_usable(self) -> None:
        now = datetime.now(UTC)
        record = self._record(FactType.DISTANCE, 7.6, valid_until=now - timedelta(seconds=120))
        report = self.verifier.verify(
            _intent(), self.candidates["bj-yanqing-001"], [record], now=now
        )
        distance = next(check for check in report.checks if check.constraint == "max_distance_km")
        assert distance.status == "PASS"
        assert distance.stale is True
        assert any("陈旧证据" in tradeoff for tradeoff in report.tradeoffs)

    def test_stale_static_fact_beyond_grace_is_unknown(self) -> None:
        now = datetime.now(UTC)
        record = self._record(FactType.DISTANCE, 7.6, valid_until=now - timedelta(hours=3))
        report = self.verifier.verify(
            _intent(), self.candidates["bj-yanqing-001"], [record], now=now
        )
        distance = next(check for check in report.checks if check.constraint == "max_distance_km")
        assert distance.status == "UNKNOWN"

    def test_stale_risk_fact_never_usable(self) -> None:
        now = datetime.now(UTC)
        record = self._record(FactType.RISK_TYPES, [], valid_until=now - timedelta(seconds=5))
        report = self.verifier.verify(
            _intent(), self.candidates["bj-yanqing-001"], [record], now=now
        )
        risk = next(check for check in report.checks if check.constraint == "avoid_risk_types")
        assert risk.status == "UNKNOWN"
        assert risk.stale is False

    def test_conflict_evidence_is_unknown(self) -> None:
        record = self._record(FactType.DISTANCE, 7.6, valid_until=None)
        record = record.model_copy(update={"status": FactStatus.CONFLICT})
        report = self.verifier.verify(_intent(), self.candidates["bj-yanqing-001"], [record])
        distance = next(check for check in report.checks if check.constraint == "max_distance_km")
        assert distance.status == "UNKNOWN"


class TestSoftConstraints:
    def setup_method(self) -> None:
        self.verifier = RouteVerifier()
        self.candidates = _candidates()
        self.evidence = _evidence()

    def test_soft_failure_does_not_block_hard_pass(self) -> None:
        intent = _intent(
            max_duration_minutes=100,
            required_facilities=["water_source", "store", "restroom"],
            min_rating=4.0,
        )
        report = self.verifier.verify(
            intent, self.candidates["bj-yanqing-001"], self.evidence["bj-yanqing-001"]
        )
        assert report.hard_pass is True
        duration = next(
            check for check in report.checks if check.constraint == "max_duration_minutes"
        )
        assert duration.status == "FAIL"
        facilities = next(
            check for check in report.checks if check.constraint == "required_facilities"
        )
        assert facilities.status == "FAIL"
        assert report.soft_score < 1.0

    def test_no_soft_constraints_scores_full(self) -> None:
        report = self.verifier.verify(
            _intent(), self.candidates["bj-yanqing-001"], self.evidence["bj-yanqing-001"]
        )
        assert report.soft_score == 1.0
