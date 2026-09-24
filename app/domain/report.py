from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.domain.evidence import EvidenceRecord, FactStatus, FactType


class CheckStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class DecisionStatus(StrEnum):
    VERIFIED_MATCHES = "VERIFIED_MATCHES"
    NO_MATCH = "NO_MATCH"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class ExecutionMode(StrEnum):
    DETERMINISTIC_PREVIEW = "deterministic_preview"
    LOCAL_ASYNC = "local_async"
    STRUCTURED_FALLBACK = "structured_fallback"


class ConstraintCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    constraint: str
    strength: str
    status: CheckStatus
    expected: Any = None
    actual: Any = None
    fact_type: FactType | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    reason: str
    stale: bool = False


class CandidateReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    route_id: str
    title: str
    source_kind: str
    checks: list[ConstraintCheck]
    hard_pass: bool
    soft_score: float = Field(ge=0, le=1)
    tradeoffs: list[str] = Field(default_factory=list)
    evidence: list[EvidenceRecord] = Field(default_factory=list)


class DecisionReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report_version: str = "decision-report.v1"
    run_id: str | None = None
    status: DecisionStatus
    execution_mode: ExecutionMode
    intent_fingerprint: str
    policy_version: str
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    candidates: list[CandidateReport] = Field(default_factory=list)
    verified_route_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    budget: dict[str, Any] = Field(default_factory=dict)

    @property
    def has_verified_match(self) -> bool:
        return self.status == DecisionStatus.VERIFIED_MATCHES

    @staticmethod
    def status_from_candidates(candidates: list[CandidateReport]) -> DecisionStatus:
        if any(candidate.hard_pass for candidate in candidates):
            return DecisionStatus.VERIFIED_MATCHES
        if any(
            check.status == CheckStatus.UNKNOWN
            for candidate in candidates
            for check in candidate.checks
            if check.strength == "hard"
        ):
            return DecisionStatus.INSUFFICIENT_EVIDENCE
        return DecisionStatus.NO_MATCH


def evidence_map(records: list[EvidenceRecord]) -> dict[tuple[str, FactType], EvidenceRecord]:
    return {(record.route_id, record.fact_type): record for record in records}


def is_hard_unknown(check: ConstraintCheck) -> bool:
    return check.strength == "hard" and check.status == CheckStatus.UNKNOWN


def status_from_fact(record: EvidenceRecord | None) -> CheckStatus:
    if record is None:
        return CheckStatus.UNKNOWN
    if record.status in {
        FactStatus.UNAVAILABLE,
        FactStatus.NOT_FOUND,
        FactStatus.CONFLICT,
        FactStatus.STALE,
    }:
        return CheckStatus.UNKNOWN
    return CheckStatus.PASS
