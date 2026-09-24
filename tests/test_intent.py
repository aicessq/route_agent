from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.domain.intent import RouteIntent


def _base_payload() -> dict:
    return {
        "location": "北京·延庆",
        "max_distance_km": 8,
        "max_elevation_gain_m": 300,
        "avoid_risk_types": ["risk_cliff_exposure"],
    }


class TestRouteIntent:
    def test_fingerprint_is_deterministic(self) -> None:
        first = RouteIntent.model_validate(_base_payload())
        second = RouteIntent.model_validate(_base_payload())
        assert first.fingerprint() == second.fingerprint()

    def test_fingerprint_changes_with_constraints(self) -> None:
        first = RouteIntent.model_validate(_base_payload())
        payload = _base_payload()
        payload["max_distance_km"] = 10
        second = RouteIntent.model_validate(payload)
        assert first.fingerprint() != second.fingerprint()

    def test_risk_constraint_cannot_be_relaxed(self) -> None:
        payload = _base_payload()
        payload["relaxation"] = {
            "avoid_risk_types": {"authorized": True, "max_value": 1, "order": 0}
        }
        with pytest.raises(ValidationError, match="risk constraints"):
            RouteIntent.model_validate(payload)

    def test_authorized_relaxation_requires_max_value(self) -> None:
        payload = _base_payload()
        payload["relaxation"] = {"max_distance_km": {"authorized": True}}
        with pytest.raises(ValidationError, match="max_value"):
            RouteIntent.model_validate(payload)

    def test_max_value_requires_authorization(self) -> None:
        payload = _base_payload()
        payload["relaxation"] = {"max_distance_km": {"authorized": False, "max_value": 10}}
        with pytest.raises(ValidationError, match="authorized"):
            RouteIntent.model_validate(payload)

    def test_relaxation_order_must_reference_declared_field(self) -> None:
        payload = _base_payload()
        payload["relaxation"] = {"max_distance_km": {"authorized": True, "max_value": 10}}
        payload["relaxation_order"] = ["max_duration_minutes"]
        with pytest.raises(ValidationError, match="relaxation_order"):
            RouteIntent.model_validate(payload)

    def test_child_ages_bounded(self) -> None:
        payload = _base_payload()
        payload["participants"] = {"children": True, "child_ages": [8, 19]}
        with pytest.raises(ValidationError):
            RouteIntent.model_validate(payload)

    def test_required_fact_fields_mapping(self) -> None:
        intent = RouteIntent.model_validate(_base_payload())
        assert intent.required_fact_fields() == {"distance_km", "elevation_gain_m", "risk_types"}

    def test_extra_fields_rejected(self) -> None:
        payload = _base_payload()
        payload["unexpected_field"] = True
        with pytest.raises(ValidationError):
            RouteIntent.model_validate(payload)
