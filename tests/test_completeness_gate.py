from __future__ import annotations

from app.domain.intent import RouteIntent
from app.services.completeness_gate import CompletenessGate


def _intent(**overrides) -> RouteIntent:
    payload = {"location": "北京", "max_distance_km": 8, "max_elevation_gain_m": 300}
    payload.update(overrides)
    return RouteIntent.model_validate(payload)


class TestCompletenessGate:
    def setup_method(self) -> None:
        self.gate = CompletenessGate()

    def test_missing_location_asks(self) -> None:
        question = self.gate.check(_intent(location=""))
        assert question is not None
        assert question.field == "location"
        assert question.kind == "missing_field"

    def test_missing_constraints_asks(self) -> None:
        question = self.gate.check(_intent(max_distance_km=None, max_elevation_gain_m=None))
        assert question is not None
        assert question.field == "constraints"
        assert question.kind == "missing_constraints"

    def test_complete_intent_passes(self) -> None:
        assert self.gate.check(_intent()) is None

    def test_only_soft_constraints_still_passes_gate(self) -> None:
        # 软约束也算可验证条件；门禁不因“只有软约束”而追问。
        intent = _intent(max_distance_km=None, max_elevation_gain_m=None, min_rating=4.0)
        assert self.gate.check(intent) is None
