from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.domain.evidence import FactType


class FactCriticality(StrEnum):
    HARD = "hard"
    SOFT = "soft"
    CONTEXT = "context"


@dataclass(frozen=True, slots=True)
class FactPolicy:
    fact_type: FactType
    unit: str
    criticality: FactCriticality
    allow_stale_within_grace: bool
    stale_grace_seconds: int
    source_priority: tuple[str, ...]


POLICY_VERSION = "fact-policy.v1"

FACT_POLICIES: dict[FactType, FactPolicy] = {
    FactType.DISTANCE: FactPolicy(
        fact_type=FactType.DISTANCE,
        unit="km",
        criticality=FactCriticality.HARD,
        allow_stale_within_grace=True,
        stale_grace_seconds=3600,
        source_priority=("public_track_stats", "curated_route", "fixture"),
    ),
    FactType.ELEVATION_GAIN: FactPolicy(
        fact_type=FactType.ELEVATION_GAIN,
        unit="m",
        criticality=FactCriticality.HARD,
        allow_stale_within_grace=True,
        stale_grace_seconds=3600,
        source_priority=("public_track_stats", "curated_route", "fixture"),
    ),
    FactType.RISK_TYPES: FactPolicy(
        fact_type=FactType.RISK_TYPES,
        unit="enum[]",
        criticality=FactCriticality.HARD,
        allow_stale_within_grace=False,
        stale_grace_seconds=0,
        source_priority=("structured_risk_poi", "public_track_annotation", "fixture"),
    ),
    FactType.DURATION: FactPolicy(
        fact_type=FactType.DURATION,
        unit="minutes",
        criticality=FactCriticality.SOFT,
        allow_stale_within_grace=True,
        stale_grace_seconds=3600,
        source_priority=("public_track_stats", "curated_route", "fixture"),
    ),
    FactType.FACILITIES: FactPolicy(
        fact_type=FactType.FACILITIES,
        unit="enum[]",
        criticality=FactCriticality.SOFT,
        allow_stale_within_grace=True,
        stale_grace_seconds=900,
        source_priority=("route_poi", "openstreetmap", "fixture"),
    ),
    FactType.RATING: FactPolicy(
        fact_type=FactType.RATING,
        unit="stars",
        criticality=FactCriticality.SOFT,
        allow_stale_within_grace=True,
        stale_grace_seconds=86400,
        source_priority=("route_rating", "fixture"),
    ),
    FactType.WEATHER: FactPolicy(
        fact_type=FactType.WEATHER,
        unit="forecast",
        criticality=FactCriticality.CONTEXT,
        allow_stale_within_grace=False,
        stale_grace_seconds=0,
        source_priority=("weather_provider", "fixture"),
    ),
}


def policy_for(fact_type: FactType) -> FactPolicy:
    return FACT_POLICIES[fact_type]
