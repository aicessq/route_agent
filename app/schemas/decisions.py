from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.domain.intent import RouteIntent
from app.domain.report import DecisionReport


class DecisionPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: RouteIntent
    candidate_limit: int = Field(default=10, ge=1, le=50)


class DecisionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: RouteIntent
    candidate_limit: int = Field(default=10, ge=1, le=50)


class DecisionAccepted(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision_id: str
    status: str = "QUEUED"
    poll_after_ms: int = Field(default=500, ge=100)


class DecisionStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision_id: str
    status: str
    created_at: datetime
    updated_at: datetime
    report: DecisionReport | None = None
    error: str | None = None
    poll_after_ms: int = Field(default=1000, ge=100)
    execution_mode: str = "local_async"


class DecisionCancelResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision_id: str
    status: str


class DecisionSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision_id: str
    summary: str
    report_fingerprint: str
    metadata: dict[str, Any] = Field(default_factory=dict)
