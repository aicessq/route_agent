from __future__ import annotations

import hashlib
import json
from datetime import date
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class RiskType(StrEnum):
    CLIFF_EXPOSURE = "risk_cliff_exposure"
    RIVER_CROSSING = "risk_river_crossing"
    ROAD_CLOSURE = "risk_road_closure"
    LANDSLIDE = "risk_landslide"
    ICE = "risk_ice"


class FacilityType(StrEnum):
    WATER_SOURCE = "water_source"
    STORE = "store"
    RESTROOM = "restroom"
    CAMP = "camp"


class RelaxationAuthorization(BaseModel):
    model_config = ConfigDict(extra="forbid")

    authorized: bool = False
    max_value: float | None = None
    order: int = Field(default=0, ge=0)
    source_text: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_bound(self) -> RelaxationAuthorization:
        if self.authorized and self.max_value is None:
            raise ValueError("authorized relaxation requires max_value")
        if not self.authorized and self.max_value is not None:
            raise ValueError("max_value requires authorized relaxation")
        return self


class Participants(BaseModel):
    model_config = ConfigDict(extra="forbid")

    children: bool = False
    child_ages: list[int] = Field(default_factory=list, max_length=8)

    @field_validator("child_ages")
    @classmethod
    def validate_child_ages(cls, ages: list[int]) -> list[int]:
        if any(age < 0 or age > 18 for age in ages):
            raise ValueError("child ages must be between 0 and 18")
        return ages


class RouteIntent(BaseModel):
    """V1 的固定领域 IR；模型或表单只能生成该类型，不能直接生成工具调用。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: str = "route-intent.v1"
    location: str = Field(min_length=1, max_length=200)
    travel_date: date | None = None
    participants: Participants = Field(default_factory=Participants)

    # V1 硬约束
    max_distance_km: float | None = Field(default=None, gt=0, le=500)
    max_elevation_gain_m: float | None = Field(default=None, gt=0, le=10000)
    avoid_risk_types: list[RiskType] = Field(default_factory=list, max_length=20)

    # V1 软偏好
    max_duration_minutes: float | None = Field(default=None, gt=0, le=1440)
    required_facilities: list[FacilityType] = Field(default_factory=list, max_length=20)
    min_rating: float | None = Field(default=None, ge=0, le=5)

    relaxation_order: list[str] = Field(default_factory=list, max_length=8)
    relaxation: dict[str, RelaxationAuthorization] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_relaxation(self) -> RouteIntent:
        forbidden = set(self.relaxation).intersection({"avoid_risk_types"})
        if forbidden:
            raise ValueError("risk constraints cannot be relaxed automatically")
        unknown = set(self.relaxation_order) - set(self.relaxation)
        if unknown:
            raise ValueError("relaxation_order contains an undeclared field")
        return self

    def fingerprint(self) -> str:
        payload = self.model_dump(mode="json")
        encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def relaxation_for(self, field_name: str) -> RelaxationAuthorization | None:
        return self.relaxation.get(field_name)

    def required_fact_fields(self) -> set[str]:
        fields: set[str] = set()
        if self.max_distance_km is not None:
            fields.add("distance_km")
        if self.max_elevation_gain_m is not None:
            fields.add("elevation_gain_m")
        if self.avoid_risk_types:
            fields.add("risk_types")
        if self.max_duration_minutes is not None:
            fields.add("duration_minutes")
        if self.required_facilities:
            fields.add("facilities")
        if self.min_rating is not None:
            fields.add("rating")
        return fields

    def as_public_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)
