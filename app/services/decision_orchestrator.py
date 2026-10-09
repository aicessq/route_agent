"""决策编排器：编译 → 门禁 → 决策 → 有界修复 → 终态。

运行协议：
- 每个 Run 的推进逻辑集中在这里；存储（内存或 MySQL）只保存状态。
- WAITING_USER 是暂停点：任务结束，用户应答后以新 intent_version 重新驱动。
- 修复动作执行后必须通过进展门，否则停止，不无限“思考”。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from app.domain.intent import RouteIntent
from app.domain.report import DecisionReport, DecisionStatus, RelaxationApplied
from app.runtime.state import RunStatus
from app.services.completeness_gate import CompletenessGate
from app.services.decision_service import DecisionService
from app.services.intent_compiler import CompileOutcome, IntentCompiler
from app.services.llm.base import CostReport, RunCostRecorder
from app.services.repair import (
    RepairAction,
    RepairPlanner,
    count_unknown_hard,
    has_progress,
    summarize_context,
    validate_proposal,
)

if TYPE_CHECKING:  # 避免运行时循环导入，仅用于类型标注
    from app.runtime.local_store import LocalDecision

_STEP_COMPILE = "COMPILE_INTENT"
_STEP_GATE = "COMPLETENESS_GATE"
_STEP_DECIDE = "RETRIEVE_VERIFY"
_STEP_REPAIR = "REPAIR"
_STEP_FINAL = "FINALIZED"


class DecisionOrchestrator:
    """编排器持有共享 LLM 客户端；每个 Run 用 RunCostRecorder 包装后按运行记账。"""

    def __init__(
        self,
        service: DecisionService,
        *,
        llm_client: object | None = None,
        primary_model: str | None = None,
        fallback_model: str | None = None,
        gate: CompletenessGate | None = None,
        max_repair_rounds: int = 2,
        max_candidates: int = 50,
        initial_candidate_limit: int = 10,
    ) -> None:
        self.service = service
        self.llm_client = llm_client
        self.primary_model = primary_model
        self.fallback_model = fallback_model
        self.gate = gate or CompletenessGate()
        self.max_repair_rounds = max_repair_rounds
        self.max_candidates = max_candidates
        self.initial_candidate_limit = initial_candidate_limit

    def _run_scoped(self, item: LocalDecision) -> tuple[IntentCompiler | None, RepairPlanner]:
        """为本次运行构建记账包装的编译器与规划器。"""
        if self.llm_client is None:
            return None, RepairPlanner(None, model=None)
        wrapped = RunCostRecorder(self.llm_client, item.cost)
        compiler = IntentCompiler(
            wrapped,
            primary_model=self.primary_model or "default",
            fallback_model=self.fallback_model,
        )
        planner = RepairPlanner(wrapped, model=self.primary_model)
        return compiler, planner

    async def run(self, item: LocalDecision) -> None:
        """推进一个 Run 直到终态或 WAITING_USER。"""
        # 仅从 QUEUED 进入 RUNNING：排队期间已请求取消的任务不被覆盖。
        if item.status == RunStatus.QUEUED:
            item.status = RunStatus.RUNNING
        item.updated_at = datetime.now(UTC)
        try:
            await self._compile(item)
            await self._gate(item)
            if item.status == RunStatus.WAITING_USER:
                return
            await self._decide_and_repair(item)
            self._finalize(item)
        except Exception as exc:  # noqa: BLE001 - Run 状态必须落为 FAILED
            item.status = RunStatus.FAILED
            item.error = f"{type(exc).__name__}: {exc}"
            item.updated_at = datetime.now(UTC)

    # ---- 步骤实现 ----

    async def _compile(self, item: LocalDecision) -> None:
        item.current_step = _STEP_COMPILE
        if item.intent is not None:
            return  # 结构化创建或已编译（应答恢复），跳过编译
        if not item.query:
            raise ValueError("run requires either query or intent")
        compiler, _ = self._run_scoped(item)
        outcome: CompileOutcome
        if compiler is not None:
            outcome = await compiler.compile(item.query)
        else:
            from app.services.intent_compiler import extract_by_rules

            outcome = CompileOutcome(
                intent=extract_by_rules(item.query),
                source="rule_extraction",
                detail="llm_not_configured",
            )
        item.compile_source = outcome.source
        item.intent = outcome.intent
        if item.intent is None:
            # 规则通道也提取失败（无任何可识别条件）：交给门禁追问。
            item.intent = RouteIntent.model_validate({"location": ""})
            item.compile_source = f"{outcome.source}:empty"

    async def _gate(self, item: LocalDecision) -> None:
        assert item.intent is not None
        item.current_step = _STEP_GATE
        question = self.gate.check(item.intent)
        if question is not None and item.question_rounds < 2:
            item.question_rounds += 1
            item.question = {"field": question.field, "text": question.text, "kind": question.kind}
            item.status = RunStatus.WAITING_USER
            item.updated_at = datetime.now(UTC)
            return
        item.question = None

    async def _decide_and_repair(self, item: LocalDecision) -> None:
        assert item.intent is not None
        item.current_step = _STEP_DECIDE
        candidate_limit = item.candidate_limit or self.initial_candidate_limit
        report = await self.service.decide(
            item.intent,
            candidate_limit=candidate_limit,
            run_id=item.decision_id,
        )
        item.report = report

        # 有界修复循环：预算与进展门双约束；规划器按运行记账。
        _, planner = self._run_scoped(item)
        for round_index in range(self.max_repair_rounds):
            context = summarize_context(
                item.intent,
                report,
                candidate_limit=candidate_limit,
                max_candidates=self.max_candidates,
                asked_question=item.question is not None,
                question_rounds=item.question_rounds,
            )
            if report.status == DecisionStatus.VERIFIED_MATCHES:
                break
            proposal, source = await planner.propose(context)
            rejection = validate_proposal(proposal, context)
            if rejection is not None:
                break  # 前置条件不满足（含确定性兜底也无可行动作）
            item.current_step = _STEP_REPAIR
            item.repair_rounds = round_index + 1
            item.repair_log.append(
                {
                    "round": round_index + 1,
                    "action": proposal.action.value,
                    "field": proposal.field,
                    "source": source,
                    "reason": proposal.reason,
                }
            )
            executed = await self._execute_repair(item, proposal, candidate_limit)
            if executed is None:
                break
            if executed == "waiting_user":
                return
            after = item.report
            assert after is not None
            # 进展门：无进展立即停止，防止故障场景下的空转。
            if not has_progress(report, after):
                item.repair_log[-1]["progress"] = "none"
                report = after
                break
            item.repair_log[-1]["progress"] = "yes"
            report = after
            candidate_limit = min(candidate_limit * 2, self.max_candidates)

        item.report = self._apply_run_metadata(item, report)

    async def _execute_repair(
        self,
        item: LocalDecision,
        proposal,
        candidate_limit: int,
    ) -> str | None:
        """执行修复动作；返回 'waiting_user' / 'executed' / None（停止）。"""
        assert item.intent is not None
        action = proposal.action
        if action == RepairAction.STOP_WITH_GAPS:
            return None
        if action == RepairAction.ASK_USER:
            field = proposal.field or "constraints"
            item.question_rounds += 1
            item.question = {
                "field": field,
                "text": f"当前没有满足全部条件的路线。是否愿意放宽“{field}”？如同意请回复授权值。",
                "kind": "relaxation_consent",
            }
            item.status = RunStatus.WAITING_USER
            item.updated_at = datetime.now(UTC)
            return "waiting_user"
        if action == RepairAction.FETCH_MISSING_EVIDENCE:
            # 重新取证：重跑 decide（Provider 幂等只读，重复调用可接受）。
            item.report = await self.service.decide(
                item.intent,
                candidate_limit=candidate_limit,
                run_id=item.decision_id,
            )
            return "executed"
        if action == RepairAction.RELAX_AUTHORIZED_PREFERENCE:
            field = proposal.field or ""
            authorization = item.intent.relaxation.get(field)
            if authorization is None:
                return None
            current_value = getattr(item.intent, field, None)
            if not isinstance(current_value, (int, float)) or current_value is None:
                return None
            relaxed_intent = item.intent.model_copy(update={field: authorization.max_value})
            item.intent = relaxed_intent
            item.intent_version += 1
            item.relaxation_applied.append(
                RelaxationApplied(
                    field=field,
                    from_value=float(current_value),
                    to_value=float(authorization.max_value),
                    authorization_source="user_consent",
                    intent_version=item.intent_version,
                )
            )
            item.report = await self.service.decide(
                relaxed_intent,
                candidate_limit=candidate_limit,
                run_id=item.decision_id,
            )
            return "executed"
        if action == RepairAction.REPLACE_CANDIDATE:
            new_limit = min(candidate_limit * 2, self.max_candidates)
            item.report = await self.service.decide(
                item.intent,
                candidate_limit=new_limit,
                run_id=item.decision_id,
            )
            return "executed"
        return None

    def _apply_run_metadata(self, item: LocalDecision, report: DecisionReport) -> DecisionReport:
        cost: CostReport = item.cost
        budget = dict(report.budget)
        budget["llm"] = cost.as_dict()
        budget["repair_rounds"] = item.repair_rounds
        budget["compile_source"] = item.compile_source
        return report.model_copy(
            update={
                "budget": budget,
                "relaxation_applied": list(item.relaxation_applied),
                "repair_rounds": item.repair_rounds,
            }
        )

    def _finalize(self, item: LocalDecision) -> None:
        item.current_step = _STEP_FINAL
        report = item.report
        if report is None:
            item.status = RunStatus.FAILED
            item.error = "no report produced"
            return
        if item.status == RunStatus.CANCEL_REQUESTED:
            item.status = RunStatus.CANCELLED
            item.report = None
            return
        item.status = {
            DecisionStatus.VERIFIED_MATCHES: RunStatus.SUCCEEDED,
            DecisionStatus.NO_MATCH: RunStatus.NO_MATCH,
            DecisionStatus.INSUFFICIENT_EVIDENCE: RunStatus.INSUFFICIENT_EVIDENCE,
        }.get(report.status, RunStatus.FAILED)
        item.updated_at = datetime.now(UTC)


def unknown_hard_total(report: DecisionReport | None) -> int:
    if report is None:
        return 0
    return count_unknown_hard(report)
