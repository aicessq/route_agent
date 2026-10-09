from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.intent import RouteIntent
from app.domain.report import DecisionReport


class DecisionPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: RouteIntent
    candidate_limit: int = Field(default=10, ge=1, le=50)


class DecisionCreateRequest(BaseModel):
    """创建任务：query（自然语言，经 LLM 编译）与 intent（结构化）二选一。"""

    model_config = ConfigDict(extra="forbid")

    query: str | None = Field(default=None, min_length=1, max_length=4000)
    intent: RouteIntent | None = None
    candidate_limit: int = Field(default=10, ge=1, le=50)

    @model_validator(mode="after")
    def validate_exclusive(self) -> DecisionCreateRequest:
        if (self.query is None) == (self.intent is None):
            raise ValueError("exactly one of query / intent is required")
        return self


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
    current_step: str = ""
    question: dict[str, str] | None = None
    intent: dict[str, Any] | None = None
    intent_version: int = 1
    compile_source: str = ""
    report: DecisionReport | None = None
    error: str | None = None
    poll_after_ms: int = Field(default=1000, ge=100)
    execution_mode: str = "local_async"


class DecisionAnswerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answers: dict[str, Any] = Field(default_factory=dict)
    authorize_relaxation: dict[str, float] | None = None


class DecisionAnswerAccepted(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision_id: str
    status: str = "QUEUED"
    intent_version: int
    poll_after_ms: int = Field(default=500, ge=100)


class DecisionCancelResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision_id: str
    status: str


class DecisionSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision_id: str
    summary: str
    report_fingerprint: str
    channel: str = "template"  # template / llm
    metadata: dict[str, Any] = Field(default_factory=dict)
