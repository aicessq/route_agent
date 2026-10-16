from __future__ import annotations

from fastapi import APIRouter, Request

from app.api.common import success
from app.settings import Settings

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(request: Request) -> dict:
    settings: Settings = request.app.state.settings
    return success(
        {
            "status": "ok",
            "app": settings.app_name,
            "environment": settings.app_env,
            "version": "0.1.0",
            # 候选池装配开关：测试页据此展示当前数据来源。
            "search_provider": settings.search_provider,
        }
    )
