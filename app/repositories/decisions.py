from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)

from app.repositories.models import (
    AgentDecisionRecord,
    AgentEvidenceRecord,
    AgentOutboxRecord,
    AgentRunRecord,
    AgentRunStepRecord,
    AgentToolCallRecord,
    utcnow,
)


class DecisionRepository:
    """MySQL 真相源的仓储实现；测试用 aiosqlite 内存库驱动同一套 SQL 语义。"""

    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine
        self.sessions = async_sessionmaker(engine, expire_on_commit=False)

    @classmethod
    def for_dsn(cls, dsn: str) -> DecisionRepository:
        return cls(create_async_engine(dsn, pool_pre_ping=True))

    async def create_all(self) -> None:
        # 仅供测试与本地开发；生产一律走 Alembic 迁移。
        async with self.engine.begin() as conn:
            from app.repositories.models import Base

            await conn.run_sync(Base.metadata.create_all)

    async def create_decision(
        self,
        *,
        user_key: str,
        idempotency_key: str,
        request_fingerprint: str,
        intent_json: dict[str, Any],
    ) -> AgentDecisionRecord:
        async with self.sessions() as session:
            existing = await session.scalar(
                select(AgentDecisionRecord).where(
                    AgentDecisionRecord.user_key == user_key,
                    AgentDecisionRecord.idempotency_key == idempotency_key,
                )
            )
            if existing is not None:
                return existing
            record = AgentDecisionRecord(
                id=uuid4().hex,
                user_key=user_key,
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
                intent_json=intent_json,
                status="QUEUED",
            )
            session.add(record)
            await session.commit()
            await session.refresh(record)
            return record

    async def get_decision(self, decision_id: str) -> AgentDecisionRecord | None:
        async with self.sessions() as session:
            return await session.scalar(
                select(AgentDecisionRecord).where(AgentDecisionRecord.id == decision_id)
            )

    async def set_decision_status(self, decision_id: str, status: str) -> None:
        async with self.sessions() as session:
            await session.execute(
                update(AgentDecisionRecord)
                .where(AgentDecisionRecord.id == decision_id)
                .values(status=status)
            )
            await session.commit()

    async def create_run(
        self,
        *,
        decision_id: str,
        run_epoch: int = 0,
        intent_version: int = 1,
    ) -> AgentRunRecord:
        async with self.sessions() as session:
            record = AgentRunRecord(
                id=uuid4().hex,
                decision_id=decision_id,
                run_epoch=run_epoch,
                intent_version=intent_version,
                status="QUEUED",
            )
            session.add(record)
            await session.commit()
            await session.refresh(record)
            return record

    async def claim_run_lease(
        self,
        run_id: str,
        *,
        owner: str,
        lease_seconds: int,
    ) -> AgentRunRecord | None:
        """条件更新领取租约：同一 Run 同时只能有一个 owner。"""
        now = utcnow()
        lease_until = now + timedelta(seconds=lease_seconds)
        async with self.sessions() as session:
            result = await session.execute(
                update(AgentRunRecord)
                .where(
                    AgentRunRecord.id == run_id,
                    AgentRunRecord.status.in_(["QUEUED", "RUNNING"]),
                    (AgentRunRecord.lease_until.is_(None)) | (AgentRunRecord.lease_until <= now),
                )
                .values(lease_owner=owner, lease_until=lease_until, status="RUNNING")
            )
            await session.commit()
            if result.rowcount == 0:
                return None
        return await self.get_run(run_id)

    async def get_run(self, run_id: str) -> AgentRunRecord | None:
        async with self.sessions() as session:
            return await session.scalar(select(AgentRunRecord).where(AgentRunRecord.id == run_id))

    async def update_run(self, run_id: str, **values: Any) -> None:
        async with self.sessions() as session:
            await session.execute(
                update(AgentRunRecord).where(AgentRunRecord.id == run_id).values(**values)
            )
            await session.commit()

    async def append_step(
        self,
        *,
        run_id: str,
        step_name: str,
        step_status: str,
        attempt: int = 0,
        error_text: str | None = None,
    ) -> AgentRunStepRecord:
        async with self.sessions() as session:
            record = AgentRunStepRecord(
                run_id=run_id,
                step_name=step_name,
                step_status=step_status,
                attempt=attempt,
                error_text=error_text,
            )
            session.add(record)
            await session.commit()
            await session.refresh(record)
            return record

    async def list_steps(self, run_id: str) -> list[AgentRunStepRecord]:
        async with self.sessions() as session:
            result = await session.scalars(
                select(AgentRunStepRecord)
                .where(AgentRunStepRecord.run_id == run_id)
                .order_by(AgentRunStepRecord.id)
            )
            return list(result)

    async def insert_evidence_batch(
        self,
        run_id: str,
        records: list[dict[str, Any]],
    ) -> int:
        """证据批量写入：同批 upsert，避免高并发下的单行写放大。"""
        if not records:
            return 0
        async with self.sessions() as session:
            for payload in records:
                stmt = select(AgentEvidenceRecord.id).where(
                    AgentEvidenceRecord.run_id == run_id,
                    AgentEvidenceRecord.route_id == payload["route_id"],
                    AgentEvidenceRecord.fact_type == payload["fact_type"],
                )
                existing_id = await session.scalar(stmt)
                values = {
                    "evidence_id": payload["evidence_id"],
                    "status": payload["status"],
                    "raw_value": payload.get("raw_value"),
                    "normalized_value": payload.get("normalized_value"),
                    "unit": payload.get("unit"),
                    "source": payload["source"],
                    "source_kind": payload.get("source_kind", "public"),
                    "observed_at": payload.get("observed_at"),
                    "valid_until": payload.get("valid_until"),
                    "entity_revision": payload.get("entity_revision"),
                    "source_fingerprint": payload.get("source_fingerprint"),
                }
                if existing_id is None:
                    session.add(
                        AgentEvidenceRecord(
                            run_id=run_id,
                            route_id=payload["route_id"],
                            fact_type=payload["fact_type"],
                            **values,
                        )
                    )
                else:
                    await session.execute(
                        update(AgentEvidenceRecord)
                        .where(AgentEvidenceRecord.id == existing_id)
                        .values(**values)
                    )
            await session.commit()
        return len(records)

    async def list_evidence(self, run_id: str) -> list[AgentEvidenceRecord]:
        async with self.sessions() as session:
            result = await session.scalars(
                select(AgentEvidenceRecord).where(AgentEvidenceRecord.run_id == run_id)
            )
            return list(result)

    async def insert_tool_call(
        self,
        *,
        run_id: str,
        provider: str,
        operation: str,
        request_hash: str,
        call_state: str = "PREPARED",
        attempt: int = 0,
    ) -> AgentToolCallRecord:
        async with self.sessions() as session:
            record = AgentToolCallRecord(
                run_id=run_id,
                provider=provider,
                operation=operation,
                request_hash=request_hash,
                call_state=call_state,
                attempt=attempt,
            )
            session.add(record)
            await session.commit()
            await session.refresh(record)
            return record

    async def update_tool_call(self, tool_call_id: int, **values: Any) -> None:
        async with self.sessions() as session:
            await session.execute(
                update(AgentToolCallRecord)
                .where(AgentToolCallRecord.id == tool_call_id)
                .values(**values)
            )
            await session.commit()

    async def enqueue_outbox(
        self,
        *,
        job_type: str,
        aggregate_id: str,
        payload: dict[str, Any],
    ) -> AgentOutboxRecord:
        async with self.sessions() as session:
            record = AgentOutboxRecord(
                job_type=job_type,
                aggregate_id=aggregate_id,
                payload=payload,
                status="PENDING",
                available_at=utcnow(),
            )
            session.add(record)
            await session.commit()
            await session.refresh(record)
            return record

    async def claim_outbox(self, *, owner: str, limit: int = 10) -> list[AgentOutboxRecord]:
        """原子领取待投递任务：UPDATE ... WHERE status='PENDING' 按 rowcount 判定成功。"""
        now = utcnow()
        claimed: list[AgentOutboxRecord] = []
        async with self.sessions() as session:
            rows = (
                await session.scalars(
                    select(AgentOutboxRecord)
                    .where(
                        AgentOutboxRecord.status == "PENDING",
                        (AgentOutboxRecord.available_at.is_(None))
                        | (AgentOutboxRecord.available_at <= now),
                    )
                    .order_by(AgentOutboxRecord.id)
                    .limit(limit)
                )
            ).all()
            for row in rows:
                result = await session.execute(
                    update(AgentOutboxRecord)
                    .where(AgentOutboxRecord.id == row.id, AgentOutboxRecord.status == "PENDING")
                    .values(status="DISPATCHED", locked_at=now, attempt_count=row.attempt_count + 1)
                )
                if result.rowcount == 1:
                    await session.refresh(row)
                    claimed.append(row)
            await session.commit()
        return claimed

    async def dispose(self) -> None:
        await self.engine.dispose()
