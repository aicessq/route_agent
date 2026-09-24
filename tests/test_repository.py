from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool

from app.repositories.decisions import DecisionRepository
from app.repositories.models import utcnow


@pytest.fixture
async def repository() -> DecisionRepository:
    # 内存 SQLite + StaticPool：测试与 MySQL 共用同一仓储语义。
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    repo = DecisionRepository(engine)
    await repo.create_all()
    yield repo
    await repo.dispose()


class TestDecisionPersistence:
    async def test_create_and_get_round_trip(self, repository: DecisionRepository) -> None:
        record = await repository.create_decision(
            user_key="user-1",
            idempotency_key="key-1",
            request_fingerprint="f" * 64,
            intent_json={"location": "北京"},
        )
        loaded = await repository.get_decision(record.id)
        assert loaded is not None
        assert loaded.user_key == "user-1"
        assert loaded.intent_json == {"location": "北京"}

    async def test_idempotency_returns_existing_row(self, repository: DecisionRepository) -> None:
        first = await repository.create_decision(
            user_key="user-1",
            idempotency_key="key-1",
            request_fingerprint="f" * 64,
            intent_json={},
        )
        second = await repository.create_decision(
            user_key="user-1",
            idempotency_key="key-1",
            request_fingerprint="f" * 64,
            intent_json={},
        )
        assert first.id == second.id

    async def test_status_update(self, repository: DecisionRepository) -> None:
        record = await repository.create_decision(
            user_key="u", idempotency_key="k", request_fingerprint="f" * 64, intent_json={}
        )
        await repository.set_decision_status(record.id, "SUCCEEDED")
        loaded = await repository.get_decision(record.id)
        assert loaded is not None and loaded.status == "SUCCEEDED"


class TestRunLease:
    async def test_claim_and_takeover(self, repository: DecisionRepository) -> None:
        decision = await repository.create_decision(
            user_key="u", idempotency_key="k", request_fingerprint="f" * 64, intent_json={}
        )
        run = await repository.create_run(decision_id=decision.id)

        owner_a = await repository.claim_run_lease(run.id, owner="worker-a", lease_seconds=30)
        assert owner_a is not None
        assert owner_a.lease_owner == "worker-a"

        # 租约未过期时第二个 worker 不能抢占。
        owner_b = await repository.claim_run_lease(run.id, owner="worker-b", lease_seconds=30)
        assert owner_b is None

        # 租约过期后允许接管。
        await repository.update_run(run.id, lease_until=utcnow() - timedelta(seconds=1))
        owner_b = await repository.claim_run_lease(run.id, owner="worker-b", lease_seconds=30)
        assert owner_b is not None
        assert owner_b.lease_owner == "worker-b"

    async def test_steps_append_and_list(self, repository: DecisionRepository) -> None:
        decision = await repository.create_decision(
            user_key="u", idempotency_key="k", request_fingerprint="f" * 64, intent_json={}
        )
        run = await repository.create_run(decision_id=decision.id)
        await repository.append_step(
            run_id=run.id, step_name="RETRIEVE_CANDIDATES", step_status="SUCCEEDED"
        )
        await repository.append_step(run_id=run.id, step_name="VERIFY", step_status="RUNNING")
        steps = await repository.list_steps(run.id)
        assert [step.step_name for step in steps] == ["RETRIEVE_CANDIDATES", "VERIFY"]


class TestEvidenceBatch:
    async def test_batch_upsert_is_idempotent(self, repository: DecisionRepository) -> None:
        decision = await repository.create_decision(
            user_key="u", idempotency_key="k", request_fingerprint="f" * 64, intent_json={}
        )
        run = await repository.create_run(decision_id=decision.id)
        records = [
            {
                "evidence_id": "ev-1",
                "route_id": "route-1",
                "fact_type": "distance_km",
                "status": "OK",
                "raw_value": 7.6,
                "normalized_value": 7.6,
                "unit": "km",
                "source": "fixture",
            },
            {
                "evidence_id": "ev-2",
                "route_id": "route-1",
                "fact_type": "risk_types",
                "status": "OK",
                "raw_value": [],
                "normalized_value": [],
                "unit": "enum[]",
                "source": "fixture",
            },
        ]
        await repository.insert_evidence_batch(run.id, records)
        await repository.insert_evidence_batch(run.id, records)
        stored = await repository.list_evidence(run.id)
        assert len(stored) == 2

    async def test_batch_update_overwrites_value(self, repository: DecisionRepository) -> None:
        decision = await repository.create_decision(
            user_key="u", idempotency_key="k", request_fingerprint="f" * 64, intent_json={}
        )
        run = await repository.create_run(decision_id=decision.id)
        await repository.insert_evidence_batch(
            run.id,
            [
                {
                    "evidence_id": "ev-1",
                    "route_id": "route-1",
                    "fact_type": "distance_km",
                    "status": "OK",
                    "raw_value": 7.6,
                    "normalized_value": 7.6,
                    "unit": "km",
                    "source": "fixture",
                }
            ],
        )
        await repository.insert_evidence_batch(
            run.id,
            [
                {
                    "evidence_id": "ev-1",
                    "route_id": "route-1",
                    "fact_type": "distance_km",
                    "status": "STALE",
                    "raw_value": 7.6,
                    "normalized_value": 7.6,
                    "unit": "km",
                    "source": "fixture",
                }
            ],
        )
        stored = await repository.list_evidence(run.id)
        assert len(stored) == 1
        assert stored[0].status == "STALE"


class TestToolCallLedger:
    async def test_tool_call_state_transitions(self, repository: DecisionRepository) -> None:
        decision = await repository.create_decision(
            user_key="u", idempotency_key="k", request_fingerprint="f" * 64, intent_json={}
        )
        run = await repository.create_run(decision_id=decision.id)
        call = await repository.insert_tool_call(
            run_id=run.id,
            provider="local_route_search",
            operation="collect",
            request_hash="h" * 64,
        )
        assert call.call_state == "PREPARED"
        await repository.update_tool_call(call.id, call_state="RUNNING", started_at=utcnow())
        await repository.update_tool_call(
            call.id, call_state="UNKNOWN", error_kind="worker_crashed"
        )


class TestOutbox:
    async def test_claim_outbox_dispatches_once(self, repository: DecisionRepository) -> None:
        await repository.enqueue_outbox(
            job_type="run_step", aggregate_id="agg-1", payload={"step": "VERIFY"}
        )
        await repository.enqueue_outbox(
            job_type="run_step", aggregate_id="agg-2", payload={"step": "FINALIZE"}
        )
        first = await repository.claim_outbox(owner="worker-a", limit=1)
        assert len(first) == 1
        assert first[0].status == "DISPATCHED"
        # 同一条不能被二次领取。
        second = await repository.claim_outbox(owner="worker-b", limit=10)
        assert [row.aggregate_id for row in second] == ["agg-2"]
        third = await repository.claim_outbox(owner="worker-a", limit=10)
        assert third == []
