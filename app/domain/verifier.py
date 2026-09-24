from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime

from app.domain.evidence import EvidenceRecord, FactStatus, FactType
from app.domain.intent import FacilityType, RiskType, RouteIntent
from app.domain.policies import policy_for
from app.domain.report import CandidateReport, CheckStatus, ConstraintCheck
from app.domain.routes import RouteCandidate

VERIFIER_VERSION = "route-verifier.v1"


class RouteVerifier:
    """确定性验证器：只做规则判断，不调用模型、不发网络请求、不改输入证据。"""

    def verify(
        self,
        intent: RouteIntent,
        candidate: RouteCandidate,
        evidence: Iterable[EvidenceRecord],
        *,
        now: datetime | None = None,
    ) -> CandidateReport:
        records = {
            record.fact_type: record.with_expiry_status(now)
            for record in evidence
            if record.route_id == candidate.route_id
        }
        checks: list[ConstraintCheck] = []
        tradeoffs: list[str] = []

        if intent.max_distance_km is not None:
            checks.append(
                self._numeric_check(
                    constraint="max_distance_km",
                    expected=intent.max_distance_km,
                    record=records.get(FactType.DISTANCE),
                    operator=lambda actual: actual <= intent.max_distance_km,
                    fact_type=FactType.DISTANCE,
                    hard=True,
                    now=now,
                )
            )

        if intent.max_elevation_gain_m is not None:
            checks.append(
                self._numeric_check(
                    constraint="max_elevation_gain_m",
                    expected=intent.max_elevation_gain_m,
                    record=records.get(FactType.ELEVATION_GAIN),
                    operator=lambda actual: actual <= intent.max_elevation_gain_m,
                    fact_type=FactType.ELEVATION_GAIN,
                    hard=True,
                    now=now,
                )
            )

        if intent.avoid_risk_types:
            checks.append(
                self._risk_check(intent.avoid_risk_types, records.get(FactType.RISK_TYPES), now=now)
            )

        if intent.max_duration_minutes is not None:
            checks.append(
                self._numeric_check(
                    constraint="max_duration_minutes",
                    expected=intent.max_duration_minutes,
                    record=records.get(FactType.DURATION),
                    operator=lambda actual: actual <= intent.max_duration_minutes,
                    fact_type=FactType.DURATION,
                    hard=False,
                    now=now,
                )
            )

        if intent.required_facilities:
            checks.append(
                self._facility_check(
                    intent.required_facilities, records.get(FactType.FACILITIES), now=now
                )
            )

        if intent.min_rating is not None:
            checks.append(
                self._numeric_check(
                    constraint="min_rating",
                    expected=intent.min_rating,
                    record=records.get(FactType.RATING),
                    operator=lambda actual: actual >= intent.min_rating,
                    fact_type=FactType.RATING,
                    hard=False,
                    now=now,
                )
            )

        hard_checks = [check for check in checks if check.strength == "hard"]
        has_hard_unknown = any(check.status == CheckStatus.UNKNOWN for check in hard_checks)
        hard_pass = not has_hard_unknown and all(
            check.status == CheckStatus.PASS for check in hard_checks
        )

        for check in checks:
            if check.status == CheckStatus.FAIL and check.strength == "soft":
                tradeoffs.append(f"{check.constraint} 未满足")
            if check.status == CheckStatus.UNKNOWN:
                tradeoffs.append(f"{check.constraint} 证据不足")
            if check.stale:
                tradeoffs.append(f"{check.constraint} 使用了宽限期内陈旧证据")

        return CandidateReport(
            route_id=candidate.route_id,
            title=candidate.title,
            source_kind=candidate.source_kind.value,
            checks=checks,
            hard_pass=hard_pass,
            soft_score=self._soft_score(checks),
            tradeoffs=tradeoffs,
            evidence=list(records.values()),
        )

    def _usable_record(
        self,
        record: EvidenceRecord | None,
        now: datetime | None,
    ) -> tuple[EvidenceRecord | None, bool]:
        """返回 (可用记录, 是否陈旧)。硬约束证据缺失、冲突或超出宽限期均返回 None。"""
        if record is None:
            return None, False
        policy = policy_for(record.fact_type)
        if record.status == FactStatus.OK:
            return record, False
        if record.status == FactStatus.STALE and policy.allow_stale_within_grace:
            if now is None or record.valid_until is None:
                return record, True
            seconds_stale = (now - record.valid_until).total_seconds()
            if seconds_stale <= policy.stale_grace_seconds:
                return record, True
            return None, False
        return None, False

    def _numeric_check(
        self,
        *,
        constraint: str,
        expected: float,
        record: EvidenceRecord | None,
        operator: Callable[[float], bool],
        fact_type: FactType,
        hard: bool,
        now: datetime | None,
    ) -> ConstraintCheck:
        strength = "hard" if hard else "soft"
        usable, stale = self._usable_record(record, now)
        if usable is None or not isinstance(usable.normalized_value, (int, float)):
            return ConstraintCheck(
                constraint=constraint,
                strength=strength,
                status=CheckStatus.UNKNOWN,
                expected=expected,
                actual=None if record is None else record.normalized_value,
                fact_type=fact_type,
                evidence_ids=[] if record is None else [record.evidence_id],
                reason="缺少可用的规范化数值证据",
                stale=stale,
            )
        actual = float(usable.normalized_value)
        passed = operator(actual)
        return ConstraintCheck(
            constraint=constraint,
            strength=strength,
            status=CheckStatus.PASS if passed else CheckStatus.FAIL,
            expected=expected,
            actual=actual,
            fact_type=fact_type,
            evidence_ids=[usable.evidence_id],
            reason="满足阈值" if passed else "超过用户阈值",
            stale=stale,
        )

    def _risk_check(
        self,
        avoided: list[RiskType],
        record: EvidenceRecord | None,
        *,
        now: datetime | None,
    ) -> ConstraintCheck:
        expected = [risk.value for risk in avoided]
        usable, stale = self._usable_record(record, now)
        if usable is None or not isinstance(usable.normalized_value, list):
            return ConstraintCheck(
                constraint="avoid_risk_types",
                strength="hard",
                status=CheckStatus.UNKNOWN,
                expected=expected,
                actual=None if record is None else record.normalized_value,
                fact_type=FactType.RISK_TYPES,
                evidence_ids=[] if record is None else [record.evidence_id],
                reason="风险证据缺失、冲突或已过期",
                stale=stale,
            )
        actual = {str(value) for value in usable.normalized_value}
        forbidden = actual.intersection({risk.value for risk in avoided})
        return ConstraintCheck(
            constraint="avoid_risk_types",
            strength="hard",
            status=CheckStatus.FAIL if forbidden else CheckStatus.PASS,
            expected=expected,
            actual=sorted(actual),
            fact_type=FactType.RISK_TYPES,
            evidence_ids=[usable.evidence_id],
            reason=(
                f"发现禁止风险: {', '.join(sorted(forbidden))}" if forbidden else "未发现指定风险"
            ),
            stale=stale,
        )

    def _facility_check(
        self,
        required: list[FacilityType],
        record: EvidenceRecord | None,
        *,
        now: datetime | None,
    ) -> ConstraintCheck:
        expected = [facility.value for facility in required]
        usable, stale = self._usable_record(record, now)
        if usable is None or not isinstance(usable.normalized_value, list):
            return ConstraintCheck(
                constraint="required_facilities",
                strength="soft",
                status=CheckStatus.UNKNOWN,
                expected=expected,
                actual=None if record is None else record.normalized_value,
                fact_type=FactType.FACILITIES,
                evidence_ids=[] if record is None else [record.evidence_id],
                reason="设施证据缺失、冲突或已过期",
                stale=stale,
            )
        actual = {str(value) for value in usable.normalized_value}
        missing = set(expected) - actual
        return ConstraintCheck(
            constraint="required_facilities",
            strength="soft",
            status=CheckStatus.FAIL if missing else CheckStatus.PASS,
            expected=expected,
            actual=sorted(actual),
            fact_type=FactType.FACILITIES,
            evidence_ids=[usable.evidence_id],
            reason=(f"缺少设施: {', '.join(sorted(missing))}" if missing else "满足设施偏好"),
            stale=stale,
        )

    @staticmethod
    def _soft_score(checks: list[ConstraintCheck]) -> float:
        soft = [check for check in checks if check.strength == "soft"]
        if not soft:
            return 1.0
        points = sum(1.0 if check.status == CheckStatus.PASS else 0.0 for check in soft)
        return round(points / len(soft), 4)
