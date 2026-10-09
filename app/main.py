from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.decisions import router as decisions_router
from app.api.health import router as health_router
from app.providers.fake import FakeRouteProvider
from app.runtime.local_store import InMemoryDecisionStore
from app.services.decision_orchestrator import DecisionOrchestrator
from app.services.decision_service import DecisionService
from app.services.summary_renderer import SummaryRenderer
from app.settings import Settings, get_settings


def create_app(
    *,
    settings: Settings | None = None,
    provider: object | None = None,
    store: InMemoryDecisionStore | None = None,
    llm_client: object | None = None,
) -> FastAPI:
    """应用工厂。

    provider/store/llm_client 是测试替身注入缝；
    默认路径：Fake Provider + （配置了 secrets/llm.env 时）真实 Agnes AI 网关。
    llm_client=None 表示自动探测：配置了密钥则用真实网关，否则禁用 LLM。
    """
    resolved_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        resolved_provider = provider or FakeRouteProvider()
        service = DecisionService(resolved_provider, resolved_provider)

        resolved_llm = llm_client
        if (
            resolved_llm is None
            and resolved_settings.llm_api_key
            and resolved_settings.llm_base_url
        ):
            # 真实 Agnes AI 网关（OpenAI 兼容）；密钥来自 secrets/llm.env。
            from app.services.llm.gateway import OpenAICompatGateway

            resolved_llm = OpenAICompatGateway(
                base_url=resolved_settings.llm_base_url,
                api_key=resolved_settings.llm_api_key,
                default_model=resolved_settings.llm_model,
                timeout_seconds=resolved_settings.llm_timeout_seconds,
            )
        orchestrator = DecisionOrchestrator(
            service,
            llm_client=resolved_llm,
            primary_model=resolved_settings.llm_model or None,
            fallback_model=resolved_settings.llm_fallback_model or None,
            max_repair_rounds=resolved_settings.max_repair_rounds,
            max_candidates=resolved_settings.max_candidates,
        )
        summary_renderer = SummaryRenderer(
            resolved_llm,
            model=resolved_settings.llm_model or None,
        )

        app.state.settings = resolved_settings
        app.state.decision_service = service
        app.state.decision_store = store or InMemoryDecisionStore()
        app.state.orchestrator = orchestrator
        app.state.summary_renderer = summary_renderer
        yield
        pending = getattr(app.state.decision_store, "_items", {})
        for item in pending.values():
            task = getattr(item, "task", None)
            if task is not None and not task.done():
                task.cancel()

    app = FastAPI(
        title="六只脚路线决策 Agent",
        version="0.2.0",
        description=(
            "约束验证型户外路线决策 Agent（公开数据原型）。\n\n"
            "- `POST /api/v1/decisions/preview`：同步确定性验证，返回结构化报告。\n"
            "- `POST /api/v1/decisions`：创建异步决策任务（202 + decisionId，需 Idempotency-Key；\n"
            "  `query` 自然语言经 LLM 编译或 `intent` 结构化二选一）。\n"
            "- `GET /api/v1/decisions/{id}`：轮询任务状态、当前步骤与追问。\n"
            "- `POST /api/v1/decisions/{id}/answers`：回答追问/授权放宽后继续。\n"
            "- `DELETE /api/v1/decisions/{id}`：协作式取消。\n"
            "- `POST /api/v1/decisions/{id}/summary`：按需摘要（LLM 优先，模板兜底）。\n\n"
            "硬约束判定全部由确定性代码完成；LLM 只做意图编译、修复提议与摘要表达。\n"
            "未配置模型服务时自动降级为规则提取 + 确定性修复策略 + 模板摘要。"
        ),
        lifespan=lifespan,
    )
    app.include_router(health_router)
    app.include_router(decisions_router, prefix=resolved_settings.api_prefix)
    return app


app = create_app()
