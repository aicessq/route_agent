"""initial schema: decisions, runs, steps, evidence, tool calls, outbox

Revision ID: 0001_initial
Revises:
Create Date: 2026-09-25

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def _timestamp_columns() -> dict:
    now = sa.text("CURRENT_TIMESTAMP")
    return {
        "created_at": sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=now
        ),
        # updated_at 由 ORM 层 onupdate 维护，DDL 层不声明 ON UPDATE 以保持方言可移植
        "updated_at": sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=now
        ),
    }


def upgrade() -> None:
    op.create_table(
        "agent_decisions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_key", sa.String(128), nullable=False, index=True),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("intent_json", sa.JSON, nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="QUEUED", index=True),
        sa.Column("error_text", sa.Text, nullable=True),
        sa.UniqueConstraint("user_key", "idempotency_key", name="uq_decision_idem"),
        *_timestamp_columns().values(),
    )
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("decision_id", sa.String(64), nullable=False, index=True),
        sa.Column("run_epoch", sa.Integer, nullable=False, server_default="0"),
        sa.Column("intent_version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("current_step", sa.String(32), nullable=False, server_default="COMPILE_INTENT"),
        sa.Column("status", sa.String(32), nullable=False, server_default="QUEUED", index=True),
        sa.Column("lease_owner", sa.String(128), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("checkpoint_ref", sa.String(128), nullable=True),
        sa.Column("error_text", sa.Text, nullable=True),
        *_timestamp_columns().values(),
    )
    op.create_table(
        "agent_run_steps",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(64), nullable=False, index=True),
        sa.Column("step_name", sa.String(32), nullable=False),
        sa.Column("step_status", sa.String(16), nullable=False, server_default="PENDING"),
        sa.Column("attempt", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error_text", sa.Text, nullable=True),
        *_timestamp_columns().values(),
    )
    op.create_table(
        "agent_evidence",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(64), nullable=False, index=True),
        sa.Column("evidence_id", sa.String(192), nullable=False),
        sa.Column("route_id", sa.String(128), nullable=False),
        sa.Column("fact_type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("raw_value", sa.JSON, nullable=True),
        sa.Column("normalized_value", sa.JSON, nullable=True),
        sa.Column("unit", sa.String(32), nullable=True),
        sa.Column("source", sa.String(128), nullable=False),
        sa.Column("source_kind", sa.String(64), nullable=False, server_default="public"),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("entity_revision", sa.String(128), nullable=True),
        sa.Column("source_fingerprint", sa.String(128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint("run_id", "route_id", "fact_type", name="uq_evidence_run_route_fact"),
    )
    op.create_table(
        "agent_tool_calls",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(64), nullable=False, index=True),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("operation", sa.String(64), nullable=False),
        sa.Column("call_state", sa.String(16), nullable=False, server_default="PREPARED"),
        sa.Column("attempt", sa.Integer, nullable=False, server_default="0"),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("error_kind", sa.String(32), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_table(
        "agent_outbox",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("job_type", sa.String(64), nullable=False),
        sa.Column("aggregate_id", sa.String(64), nullable=False),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="PENDING", index=True),
        sa.Column("attempt_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer, nullable=False, server_default="5"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.UniqueConstraint("job_type", "aggregate_id", name="uq_outbox_job_aggregate"),
        *_timestamp_columns().values(),
    )


def downgrade() -> None:
    op.drop_table("agent_outbox")
    op.drop_table("agent_tool_calls")
    op.drop_table("agent_evidence")
    op.drop_table("agent_run_steps")
    op.drop_table("agent_runs")
    op.drop_table("agent_decisions")
