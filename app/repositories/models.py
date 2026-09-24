from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class AgentDecisionRecord(Base, TimestampMixin):
    """一次用户决策；重试创建新 Run，不修改历史行。"""

    __tablename__ = "agent_decisions"
    __table_args__ = (UniqueConstraint("user_key", "idempotency_key", name="uq_decision_idem"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_key: Mapped[str] = mapped_column(String(128), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    intent_json: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), default="QUEUED", index=True)
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)


class AgentRunRecord(Base, TimestampMixin):
    """Decision 的执行尝试；lease 字段支撑多 Worker 租约接管。"""

    __tablename__ = "agent_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    decision_id: Mapped[str] = mapped_column(String(64), index=True)
    run_epoch: Mapped[int] = mapped_column(Integer, default=0)
    intent_version: Mapped[int] = mapped_column(Integer, default=1)
    current_step: Mapped[str] = mapped_column(String(32), default="COMPILE_INTENT")
    status: Mapped[str] = mapped_column(String(32), default="QUEUED", index=True)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    checkpoint_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)


class AgentRunStepRecord(Base, TimestampMixin):
    """阶段级 checkpoint：每个阶段产物落库后才能推进。"""

    __tablename__ = "agent_run_steps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    step_name: Mapped[str] = mapped_column(String(32))
    step_status: Mapped[str] = mapped_column(String(16), default="PENDING")
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)


class AgentEvidenceRecord(Base):
    """证据账本：批量写入，按 (run_id, route_id, fact_type) 唯一。"""

    __tablename__ = "agent_evidence"
    __table_args__ = (
        UniqueConstraint("run_id", "route_id", "fact_type", name="uq_evidence_run_route_fact"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    evidence_id: Mapped[str] = mapped_column(String(192))
    route_id: Mapped[str] = mapped_column(String(128))
    fact_type: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16))
    raw_value: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    normalized_value: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    unit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source: Mapped[str] = mapped_column(String(128))
    source_kind: Mapped[str] = mapped_column(String(64), default="public")
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    entity_revision: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_fingerprint: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AgentToolCallRecord(Base):
    """调用账本：PREPARED/RUNNING/SUCCEEDED/FAILED/UNKNOWN，恢复时据此去重。"""

    __tablename__ = "agent_tool_calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    provider: Mapped[str] = mapped_column(String(64))
    operation: Mapped[str] = mapped_column(String(64))
    call_state: Mapped[str] = mapped_column(String(16), default="PREPARED")
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    request_hash: Mapped[str] = mapped_column(String(64))
    error_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AgentOutboxRecord(Base, TimestampMixin):
    """事务性 Outbox：状态提交与投递同事务落库，Reconciler 修复漏投递。"""

    __tablename__ = "agent_outbox"
    __table_args__ = (UniqueConstraint("job_type", "aggregate_id", name="uq_outbox_job_aggregate"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_type: Mapped[str] = mapped_column(String(64))
    aggregate_id: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default="PENDING", index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    available_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
