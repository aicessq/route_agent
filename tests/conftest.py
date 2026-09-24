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


@pytest.fixture
def fake_provider() -> FakeRouteProvider:
    return FakeRouteProvider()


@pytest.fixture
def decision_service(fake_provider: FakeRouteProvider) -> DecisionService:
    return DecisionService(fake_provider, fake_provider)


@pytest.fixture
def client() -> TestClient:
    app = create_app()
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def slow_client() -> TestClient:
    # 延迟 Provider 用于取消语义测试：任务在运行中被取消。
    app = create_app(provider=FakeRouteProvider(delay_seconds=0.4))
    with TestClient(app) as test_client:
        yield test_client


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
