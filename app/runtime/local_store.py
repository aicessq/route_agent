"""内存任务存储：幂等、任务生命周期与追问应答合并。

阶段 4 说明：编排逻辑在 DecisionOrchestrator；本类只管理状态与任务句柄。
MySQL 持久化（阶段 3 范围）替换本类时，对外契约不变。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from app.domain.intent import RouteIntent
from app.domain.report import DecisionReport, ExecutionMode
from app.runtime.state import RunStatus
from app.services.llm.base import CostReport

Runner = Callable[["LocalDecision"], Awaitable[None]]

_ALLOWED_ANSWER_FIELDS = {
    "location",
    "travel_date",
    "max_distance_km",
    "max_elevation_gain_m",
    "max_duration_minutes",
    "min_rating",
}
_RELAXABLE_FIELDS = {
    "max_distance_km",
    "max_elevation_gain_m",
    "max_duration_minutes",
    "min_rating",
}


class IdempotencyConflictError(ValueError):
    pass


class AnswerValidationError(ValueError):
    pass


class LocalDecision:
    """一个 Decision 的内存态；字段与 MySQL 表模型一一对应。"""

    def __init__(
        self,
        *,
        decision_id: str,
        request_fingerprint: str,
        query: str | None,
        intent: RouteIntent | None,
        candidate_limit: int,
        user_key: str,
    ) -> None:
        self.decision_id = decision_id
        self.request_fingerprint = request_fingerprint
        self.query = query
        self.intent = intent
        self.candidate_limit = candidate_limit
        self.user_key = user_key
        self.status = RunStatus.QUEUED
        self.created_at = datetime.now(UTC)
        self.updated_at = self.created_at
        self.report: DecisionReport | None = None
        self.error: str | None = None
        self.task: asyncio.Task[None] | None = None
        # 阶段 4 运行元数据
        self.intent_version = 1
        self.question: dict[str, str] | None = None
        self.question_rounds = 0
        self.repair_rounds = 0
        self.repair_log: list[dict[str, Any]] = []
        self.relaxation_applied: list[Any] = []
        self.compile_source = ""
        self.current_step = "COMPILE_INTENT"
        self.cost = CostReport()

    @property
    def terminal(self) -> bool:
        return self.status in {
            RunStatus.SUCCEEDED,
            RunStatus.NO_MATCH,
            RunStatus.INSUFFICIENT_EVIDENCE,
            RunStatus.CANCELLED,
            RunStatus.FAILED,
        }


class InMemoryDecisionStore:
    def __init__(self) -> None:
        self._items: dict[str, LocalDecision] = {}
        self._idempotency: dict[tuple[str, str], tuple[str, str]] = {}
        self._lock = asyncio.Lock()

    async def create(
        self,
        *,
        user_key: str,
        idempotency_key: str,
        query: str | None,
        intent: RouteIntent | None,
        candidate_limit: int,
        runner: Runner,
    ) -> tuple[LocalDecision, bool]:
        fingerprint = intent.fingerprint() if intent is not None else f"query:{query}"
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
            item = LocalDecision(
                decision_id=uuid4().hex,
                request_fingerprint=fingerprint,
                query=query,
                intent=intent,
                candidate_limit=candidate_limit,
                user_key=user_key,
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
            if item.terminal:
                return item
            item.status = RunStatus.CANCEL_REQUESTED
            item.updated_at = datetime.now(UTC)
            return item

    async def apply_answers(
        self,
        decision_id: str,
        *,
        answers: dict[str, Any],
        authorize_relaxation: dict[str, float] | None,
        runner: Runner,
    ) -> LocalDecision:
        """合并追问应答：生成新 intent_version 并重新驱动任务。"""
        async with self._lock:
            item = self._items.get(decision_id)
            if item is None:
                raise AnswerValidationError("decision not found")
            if item.status != RunStatus.WAITING_USER or item.intent is None:
                raise AnswerValidationError("task is not waiting for user input")

        illegal = set(answers) - _ALLOWED_ANSWER_FIELDS
        if illegal:
            raise AnswerValidationError(f"unsupported answer fields: {sorted(illegal)}")
        relaxations = authorize_relaxation or {}
        illegal_relax = set(relaxations) - _RELAXABLE_FIELDS
        if illegal_relax:
            raise AnswerValidationError(f"non-relaxable fields: {sorted(illegal_relax)}")

        updates = {key: value for key, value in answers.items() if value is not None}
        merged_payload = item.intent.model_dump(mode="json")
        merged_payload.update(updates)

        existing_relax: dict[str, Any] = dict(merged_payload.get("relaxation") or {})
        existing_relax.update(
            {
                name: {"authorized": True, "max_value": float(value), "order": index}
                for index, (name, value) in enumerate(sorted(relaxations.items()))
            }
        )
        if existing_relax:
            merged_payload["relaxation"] = existing_relax
            merged_payload["relaxation_order"] = sorted(existing_relax)

        merged_payload.pop("schema_version", None)
        try:
            merged = RouteIntent.model_validate(merged_payload)
        except ValidationError as exc:
            first = exc.errors()[0]
            raise AnswerValidationError(f"merged intent invalid: {first['msg']}") from exc

        async with self._lock:
            item.intent = merged
            item.intent_version += 1
            item.question = None
            item.status = RunStatus.QUEUED
            item.updated_at = datetime.now(UTC)
            item.task = asyncio.create_task(runner(item))
            return item

    @staticmethod
    def with_preview_mode(report: DecisionReport) -> DecisionReport:
        return report.model_copy(update={"execution_mode": ExecutionMode.DETERMINISTIC_PREVIEW})
