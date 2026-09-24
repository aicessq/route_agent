from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class RunStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    WAITING_USER = "WAITING_USER"
    SUCCEEDED = "SUCCEEDED"
    NO_MATCH = "NO_MATCH"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class StepName(StrEnum):
    COMPILE_INTENT = "COMPILE_INTENT"
    RETRIEVE_CANDIDATES = "RETRIEVE_CANDIDATES"
    COLLECT_EVIDENCE = "COLLECT_EVIDENCE"
    VERIFY = "VERIFY"
    REPAIR = "REPAIR"
    FINALIZE = "FINALIZE"


class StepStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"
    SKIPPED = "SKIPPED"


class RuntimeSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    run_epoch: int = 0
    intent_version: int = 1
    current_step: StepName = StepName.COMPILE_INTENT
    status: RunStatus = RunStatus.QUEUED
    step_status: StepStatus = StepStatus.PENDING
    lease_owner: str | None = None
    lease_until: datetime | None = None
    checkpoint_ref: str | None = None
    updated_at: datetime = datetime.now(UTC)

    def lease_is_expired(self, now: datetime | None = None) -> bool:
        if self.lease_until is None:
            return True
        return self.lease_until <= (now or datetime.now(UTC))
