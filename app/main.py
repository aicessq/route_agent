from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

from app.api.decisions import router as decisions_router
from app.api.health import router as health_router
from app.providers.fake import FakeRouteProvider
from app.runtime.local_store import InMemoryDecisionStore
from app.services.decision_service import DecisionService
from app.services.summary import TemplateSummaryRenderer
from app.settings import Settings, get_settings


def create_app(
    *,
    settings: Settings | None = None,
    provider: Any | None = None,
    store: InMemoryDecisionStore | None = None,
) -> FastAPI:
    """应用工厂。provider/store 参数是测试替身注入缝，生产路径用默认实现。"""
    resolved_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # 阶段 1 只初始化内存资源；MySQL/Redis/Worker 属于阶段 3 的 lifespan 职责。
        resolved_provider = provider or FakeRouteProvider()
        app.state.settings = resolved_settings
        app.state.decision_service = DecisionService(resolved_provider, resolved_provider)
        app.state.decision_store = store or InMemoryDecisionStore()
        app.state.summary_renderer = TemplateSummaryRenderer()
        app.state.summary_cache: dict[str, str] = {}
        yield
        pending = getattr(app.state.decision_store, "_items", {})
        for item in pending.values():
            task = getattr(item, "task", None)
            if task is not None and not task.done():
                task.cancel()

    app = FastAPI(
        title="六只脚路线决策 Agent",
        version="0.1.0",
        description=(
            "约束验证型户外路线决策 Agent（公开数据原型）。\n\n"
            "- `POST /api/v1/decisions/preview`：同步确定性验证，返回结构化报告。\n"
            "- `POST /api/v1/decisions`：创建异步决策任务（202 + decisionId，"
            "需 Idempotency-Key）。\n"
            "- `GET /api/v1/decisions/{id}`：轮询任务状态与报告。\n"
            "- `DELETE /api/v1/decisions/{id}`：协作式取消。\n"
            "- `POST /api/v1/decisions/{id}/summary`：按需生成模板摘要。\n\n"
            "硬约束判定全部由确定性代码完成；LLM 不参与本阶段。"
        ),
        lifespan=lifespan,
    )
    app.include_router(health_router)
    app.include_router(decisions_router, prefix=resolved_settings.api_prefix)
    return app


app = create_app()
