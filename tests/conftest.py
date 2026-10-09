from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.providers.fake import FakeRouteProvider
from app.services.decision_service import DecisionService
from app.services.llm.fake import FakeLLMClient
from app.settings import Settings


def hermetic_settings() -> Settings:
    """测试用配置：显式清空 LLM，避免读到本地 secrets 后触网。"""
    return Settings(llm_api_key="", llm_base_url="", llm_model="", llm_fallback_model="")


@pytest.fixture
def fake_provider() -> FakeRouteProvider:
    return FakeRouteProvider()


@pytest.fixture
def decision_service(fake_provider: FakeRouteProvider) -> DecisionService:
    return DecisionService(fake_provider, fake_provider)


@pytest.fixture
def client() -> TestClient:
    # 默认客户端：无 LLM（结构化 intent 路径），保证测试不触网。
    app = create_app(settings=hermetic_settings())
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def slow_client() -> TestClient:
    # 延迟 Provider 用于取消语义测试：任务在运行中被取消。
    app = create_app(settings=hermetic_settings(), provider=FakeRouteProvider(delay_seconds=0.4))
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def scripted_llm() -> FakeLLMClient:
    """空脚本 FakeLLM；由具体测试自行 queue 输出。"""
    return FakeLLMClient(model="fake-small")


@pytest.fixture
def llm_client_factory():
    """按脚本构建带 LLM 的测试应用。"""

    def _build(outputs, *, provider=None):
        fake_llm = FakeLLMClient(outputs, model="fake-small")
        app = create_app(settings=hermetic_settings(), llm_client=fake_llm, provider=provider)
        return app, fake_llm

    return _build


@pytest.fixture
def family_intent_payload() -> dict:
    """亲子出行场景：延庆 8km/300m/避开悬崖/2h/有水源。"""
    return {
        "location": "北京·延庆",
        "participants": {"children": True, "child_ages": [8]},
        "max_distance_km": 8,
        "max_elevation_gain_m": 300,
        "avoid_risk_types": ["risk_cliff_exposure"],
        "max_duration_minutes": 120,
        "required_facilities": ["water_source", "store"],
    }
