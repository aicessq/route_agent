"""有界 Repair：受限动作集 + 服务端前置校验 + 进展门。

设计要点：
- LLM 只能从五个枚举动作中提议；服务端始终校验前置条件，越权提议被拒绝。
- LLM 不可用时由确定性策略兜底（决策权始终在代码）。
- 每轮必须减少验证缺口或引入新候选，否则停止。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from app.domain.intent import RouteIntent
from app.domain.report import CheckStatus, DecisionReport
from app.services.llm.base import LLMFailure, extract_json_object
from app.services.llm.gateway import LLMClient
from app.services.llm.prompts import (
    REPAIR_PLANNER_SYSTEM,
    REPAIR_PLANNER_USER_TEMPLATE,
)

REPAIR_PROMPT_VERSION = "repair-planner.v1"


class RepairAction(StrEnum):
    FETCH_MISSING_EVIDENCE = "FETCH_MISSING_EVIDENCE"
    RELAX_AUTHORIZED_PREFERENCE = "RELAX_AUTHORIZED_PREFERENCE"
    REPLACE_CANDIDATE = "REPLACE_CANDIDATE"
    ASK_USER = "ASK_USER"
    STOP_WITH_GAPS = "STOP_WITH_GAPS"


@dataclass(slots=True)
class RepairProposal:
    action: RepairAction
    field: str | None = None
    reason: str = ""


@dataclass(slots=True)
class RepairContext:
    """修复决策的输入快照；全部来自当前报告与意图，不引入报告外信息。"""

    intent: RouteIntent
    report: DecisionReport
    unknown_hard_count: int = 0
    verified_count: int = 0
    candidate_count: int = 0
    candidate_limit: int = 10
    max_candidates: int = 50
    asked_question: bool = False
    question_rounds: int = 0
    soft_failure_fields: list[str] = field(default_factory=list)


def count_unknown_hard(report: DecisionReport) -> int:
    return sum(
        1
        for candidate in report.candidates
        for check in candidate.checks
        if check.strength == "hard" and check.status == CheckStatus.UNKNOWN
    )


def soft_failure_fields(report: DecisionReport) -> list[str]:
    """软约束未满足的字段集合：ASK_USER（请求放宽授权）的候选集。"""
    fields: list[str] = []
    for candidate in report.candidates:
        if candidate.hard_pass:
            for check in candidate.checks:
                failed_soft = check.strength == "soft" and check.status != CheckStatus.PASS
                if failed_soft and check.constraint not in fields:
                    fields.append(check.constraint)
    return fields


def validate_proposal(proposal: RepairProposal, context: RepairContext) -> str | None:
    """服务端前置校验：返回拒绝原因；None 表示通过。LLM 提议同样必须过这里。"""
    action = proposal.action
    if action == RepairAction.FETCH_MISSING_EVIDENCE:
        if context.verified_count > 0:
            return "already has verified candidates"
        if context.unknown_hard_count == 0:
            return "no unknown hard constraints to fetch"
        return None
    if action == RepairAction.RELAX_AUTHORIZED_PREFERENCE:
        if context.verified_count > 0:
            return "already has verified candidates"
        field_name = proposal.field or ""
        authorization = context.intent.relaxation.get(field_name)
        if authorization is None or not authorization.authorized:
            return f"relaxation for {field_name!r} is not authorized"
        return None
    if action == RepairAction.REPLACE_CANDIDATE:
        if context.verified_count > 0:
            return "already has verified candidates"
        if context.candidate_count >= context.max_candidates:
            return "candidate budget exhausted"
        return None
    if action == RepairAction.ASK_USER:
        if context.question_rounds >= 2:
            return "question rounds exhausted"
        return None
    return None  # STOP_WITH_GAPS 永远合法


def has_progress(before: DecisionReport, after: DecisionReport) -> bool:
    """进展门：验证缺口减少、已验证出现或候选集扩大（且已验证不减少）。"""
    before_verified = len(before.verified_route_ids)
    after_verified = len(after.verified_route_ids)
    if after_verified > before_verified:
        return True
    if after_verified < before_verified:
        return False
    if count_unknown_hard(after) < count_unknown_hard(before):
        return True
    return len(after.candidates) > len(before.candidates)


def deterministic_proposal(context: RepairContext) -> RepairProposal:
    """确定性策略：LLM 不可用时的兜底，优先级固定且可解释。"""
    if context.verified_count > 0:
        return RepairProposal(action=RepairAction.STOP_WITH_GAPS, reason="已有已验证候选")
    if context.unknown_hard_count > 0:
        return RepairProposal(
            action=RepairAction.FETCH_MISSING_EVIDENCE,
            reason=f"{context.unknown_hard_count} 个硬约束证据缺失",
        )
    for field_name in sorted(context.intent.relaxation):
        if context.intent.relaxation[field_name].authorized:
            return RepairProposal(
                action=RepairAction.RELAX_AUTHORIZED_PREFERENCE,
                field=field_name,
                reason="用户已授权放宽",
            )
    if context.soft_failure_fields and context.question_rounds < 2 and not context.asked_question:
        return RepairProposal(
            action=RepairAction.ASK_USER,
            field=context.soft_failure_fields[0],
            reason="软约束未满足且未获放宽授权",
        )
    if context.candidate_count < context.max_candidates:
        return RepairProposal(action=RepairAction.REPLACE_CANDIDATE, reason="扩大召回范围")
    return RepairProposal(action=RepairAction.STOP_WITH_GAPS, reason="无可用修复动作")


class RepairPlanner:
    """受限规划器：LLM 提议 + 服务端校验；LLM 失败回落确定性策略。"""

    def __init__(self, client: LLMClient | None, *, model: str | None) -> None:
        self.client = client
        self.model = model

    async def propose(self, context: RepairContext) -> tuple[RepairProposal, str]:
        """返回 (proposal, source)；source ∈ llm / deterministic。"""
        if self.client is not None:
            proposal = await self._propose_via_llm(context)
            if proposal is not None:
                rejection = validate_proposal(proposal, context)
                if rejection is None:
                    return proposal, "llm"
                # LLM 越权提议被拒：回落确定性策略，并保留拒绝记录。
        return deterministic_proposal(context), "deterministic"

    async def _propose_via_llm(self, context: RepairContext) -> RepairProposal | None:
        user_prompt = REPAIR_PLANNER_USER_TEMPLATE.format(
            verified_count=context.verified_count,
            unknown_hard_count=context.unknown_hard_count,
            candidate_count=context.candidate_count,
            authorized_relaxations={
                name: {"authorized": auth.authorized, "max_value": auth.max_value}
                for name, auth in context.intent.relaxation.items()
            },
            asked_question=context.asked_question,
        )
        try:
            result = await self.client.chat_json(
                system=REPAIR_PLANNER_SYSTEM,
                user=user_prompt,
                model=self.model,
                purpose="repair_planning",
            )
            payload = extract_json_object(result.content)
        except LLMFailure:
            return None
        action_value = str(payload.get("action", "")).strip()
        try:
            action = RepairAction(action_value)
        except ValueError:
            return None
        field_value = payload.get("field")
        return RepairProposal(
            action=action,
            field=str(field_value) if field_value and str(field_value).lower() != "null" else None,
            reason=str(payload.get("reason", ""))[:200],
        )


def summarize_context(
    intent: RouteIntent, report: DecisionReport, **overrides: Any
) -> RepairContext:
    return RepairContext(
        intent=intent,
        report=report,
        unknown_hard_count=count_unknown_hard(report),
        verified_count=len(report.verified_route_ids),
        candidate_count=len(report.candidates),
        soft_failure_fields=soft_failure_fields(report),
        **overrides,
    )
