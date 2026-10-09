from __future__ import annotations

import re

from fastapi import APIRouter, Header, Request, Response

from app.api.common import error_response, success
from app.runtime.local_store import (
    AnswerValidationError,
    IdempotencyConflictError,
    InMemoryDecisionStore,
)
from app.schemas.decisions import (
    DecisionAccepted,
    DecisionAnswerAccepted,
    DecisionAnswerRequest,
    DecisionCancelResponse,
    DecisionCreateRequest,
    DecisionPreviewRequest,
    DecisionStatusResponse,
    DecisionSummaryResponse,
)
from app.services.decision_orchestrator import DecisionOrchestrator
from app.services.decision_service import DecisionService
from app.services.summary_renderer import SummaryRenderer

router = APIRouter(prefix="/decisions", tags=["decisions"])

_IDEMPOTENCY_PATTERN = re.compile(r"^[!-~]{8,128}$")


def _service(request: Request) -> DecisionService:
    return request.app.state.decision_service


def _store(request: Request) -> InMemoryDecisionStore:
    return request.app.state.decision_store


def _orchestrator(request: Request) -> DecisionOrchestrator:
    return request.app.state.orchestrator


def _summary_renderer(request: Request) -> SummaryRenderer:
    return request.app.state.summary_renderer


def _user_key(request: Request) -> str:
    # 阶段 4 仍无登录；接入 sixfoot_web 后由 HMAC 内部签名替换本占位。
    return request.headers.get("x-user-id", "local")


async def _runner(request: Request, item):  # noqa: ANN001 - LocalDecision 避免循环导入
    await _orchestrator(request).run(item)


@router.post("/preview")
async def preview_decision(payload: DecisionPreviewRequest, request: Request) -> dict:
    report = await _service(request).decide(
        payload.intent,
        candidate_limit=payload.candidate_limit,
    )
    return success(report.model_dump(mode="json"))


@router.post("", status_code=202)
async def create_decision(
    payload: DecisionCreateRequest,
    request: Request,
    response: Response,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict:
    if idempotency_key is None or not _IDEMPOTENCY_PATTERN.fullmatch(idempotency_key):
        return error_response(
            status_code=400,
            code=10001,
            message="Idempotency-Key 必须为 8-128 个可打印 ASCII 字符",
        )
    try:
        item, replayed = await _store(request).create(
            user_key=_user_key(request),
            idempotency_key=idempotency_key,
            query=payload.query,
            intent=payload.intent,
            candidate_limit=payload.candidate_limit,
            runner=lambda decision: _runner(request, decision),
        )
    except IdempotencyConflictError:
        return error_response(
            status_code=409,
            code=10002,
            message="Idempotency-Key 已被不同请求使用",
        )
    response.status_code = 202 if not replayed else 200
    return success(DecisionAccepted(decision_id=item.decision_id).model_dump(mode="json"))


@router.get("/{decision_id}")
async def get_decision(decision_id: str, request: Request) -> dict:
    item = await _store(request).get(decision_id)
    if item is None:
        return error_response(status_code=404, code=10003, message="决策任务不存在")
    return success(
        DecisionStatusResponse(
            decision_id=item.decision_id,
            status=item.status.value,
            created_at=item.created_at,
            updated_at=item.updated_at,
            current_step=item.current_step,
            question=item.question,
            intent=item.intent.as_public_dict() if item.intent is not None else None,
            intent_version=item.intent_version,
            compile_source=item.compile_source,
            report=item.report,
            error=item.error,
            execution_mode="local_async",
        ).model_dump(mode="json")
    )


@router.delete("/{decision_id}")
async def cancel_decision(decision_id: str, request: Request) -> dict:
    item = await _store(request).cancel(decision_id)
    if item is None:
        return error_response(status_code=404, code=10003, message="决策任务不存在")
    return success(
        DecisionCancelResponse(decision_id=item.decision_id, status=item.status.value).model_dump(
            mode="json"
        )
    )


@router.post("/{decision_id}/answers")
async def answer_decision(
    decision_id: str,
    payload: DecisionAnswerRequest,
    request: Request,
) -> dict:
    try:
        item = await _store(request).apply_answers(
            decision_id,
            answers=payload.answers,
            authorize_relaxation=payload.authorize_relaxation,
            runner=lambda decision: _runner(request, decision),
        )
    except AnswerValidationError as exc:
        message = str(exc)
        if message == "decision not found":
            return error_response(status_code=404, code=10003, message="决策任务不存在")
        return error_response(status_code=409, code=10005, message=message)
    return success(
        DecisionAnswerAccepted(
            decision_id=item.decision_id, intent_version=item.intent_version
        ).model_dump(mode="json")
    )


@router.post("/{decision_id}/summary")
async def summarize_decision(decision_id: str, request: Request) -> dict:
    store = _store(request)
    item = await store.get(decision_id)
    if item is None:
        return error_response(status_code=404, code=10003, message="决策任务不存在")
    if item.report is None:
        return error_response(
            status_code=409,
            code=10004,
            message="任务尚未产出可摘要的报告",
        )
    summary, channel = await _summary_renderer(request).render(item.report)
    return success(
        DecisionSummaryResponse(
            decision_id=decision_id,
            summary=summary,
            report_fingerprint=item.request_fingerprint,
            channel=channel,
        ).model_dump(mode="json")
    )
