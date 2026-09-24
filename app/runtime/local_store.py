from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from app.domain.intent import RouteIntent
from app.domain.report import DecisionReport, DecisionStatus, ExecutionMode
from app.runtime.state import RunStatus
from app.services.decision_service import DecisionService


class IdempotencyConflictError(ValueError):
    pass


@dataclass(slots=True)
class LocalDecision:
    decision_id: str
    request_fingerprint: str
    intent: RouteIntent
    status: RunStatus
    created_at: datetime
    updated_at: datetime
    report: DecisionReport | None = None
    error: str | None = None
    task: asyncio.Task[None] | None = None


class InMemoryDecisionStore:
    """初始化阶段的本地替身；正式持久化由 MySQL repository/outbox 替换。"""

    def __init__(self) -> None:
        self._items: dict[str, LocalDecision] = {}
        self._idempotency: dict[tuple[str, str], tuple[str, str]] = {}
        self._lock = asyncio.Lock()

    async def create(
        self,
        *,
        user_key: str,
        idempotency_key: str,
        intent: RouteIntent,
        runner: Callable[[LocalDecision], Awaitable[None]],
    ) -> tuple[LocalDecision, bool]:
        fingerprint = intent.fingerprint()
        key = (user_key, idempotency_key)
        async with self._lock:
            existing = self._idempotency.get(key)
            if existing is not None:
                decision_id, previous_fingerprint = existing
                if previous_fingerprint != fingerprint:
                    raise IdempotencyConflictError(
                        "Idempotency-Key was already used with a different request"
                    )
                return self._items[decision_id], True
            now = datetime.now(UTC)
            item = LocalDecision(
                decision_id=uuid4().hex,
                request_fingerprint=fingerprint,
                intent=intent,
                status=RunStatus.QUEUED,
                created_at=now,
                updated_at=now,
            )
            self._items[item.decision_id] = item
            self._idempotency[key] = (item.decision_id, fingerprint)
            item.task = asyncio.create_task(runner(item))
            return item, False

    async def get(self, decision_id: str) -> LocalDecision | None:
        async with self._lock:
            return self._items.get(decision_id)

    async def cancel(self, decision_id: str) -> LocalDecision | None:
        async with self._lock:
            item = self._items.get(decision_id)
            if item is None:
                return None
            if item.status in {
                RunStatus.SUCCEEDED,
                RunStatus.NO_MATCH,
                RunStatus.INSUFFICIENT_EVIDENCE,
                RunStatus.CANCELLED,
                RunStatus.FAILED,
            }:
                return item
            item.status = RunStatus.CANCEL_REQUESTED
            item.updated_at = datetime.now(UTC)
            return item

    async def run(self, item: LocalDecision, service: DecisionService) -> None:
        item.status = RunStatus.RUNNING
        item.updated_at = datetime.now(UTC)
        try:
            report = await service.decide(
                item.intent,
                run_id=item.decision_id,
                execution_mode=ExecutionMode.LOCAL_ASYNC,
            )
            if item.status == RunStatus.CANCEL_REQUESTED:
                item.status = RunStatus.CANCELLED
                item.report = None
            else:
                item.report = report
                item.status = {
                    DecisionStatus.VERIFIED_MATCHES: RunStatus.SUCCEEDED,
                    DecisionStatus.NO_MATCH: RunStatus.NO_MATCH,
                    DecisionStatus.INSUFFICIENT_EVIDENCE: RunStatus.INSUFFICIENT_EVIDENCE,
                }.get(report.status, RunStatus.FAILED)
        except asyncio.CancelledError:
            item.status = RunStatus.CANCELLED
            raise
        except Exception as exc:  # noqa: BLE001 - 任务状态必须落为 FAILED
            item.status = RunStatus.FAILED
            item.error = str(exc)
        finally:
            item.updated_at = datetime.now(UTC)
