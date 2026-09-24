from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class RouteSourceKind(StrEnum):
    CURATED = "curated_route"
    PUBLIC_TRACK = "public_track"
    SYNTHETIC = "synthetic_fixture"


class RouteCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    route_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=300)
    location: str = Field(min_length=1, max_length=200)
    source_kind: RouteSourceKind
    entity_revision: str
    source_fingerprint: str
    indexed_distance_km: float | None = Field(default=None, ge=0)
    indexed_elevation_gain_m: float | None = Field(default=None, ge=0)
    tags: list[str] = Field(default_factory=list)
