from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class FactType(StrEnum):
    DISTANCE = "distance_km"
    ELEVATION_GAIN = "elevation_gain_m"
    DURATION = "duration_minutes"
    RISK_TYPES = "risk_types"
    FACILITIES = "facilities"
    RATING = "rating"
    WEATHER = "weather"


class FactStatus(StrEnum):
    OK = "OK"
    STALE = "STALE"
    CONFLICT = "CONFLICT"
    UNAVAILABLE = "UNAVAILABLE"
    NOT_FOUND = "NOT_FOUND"


class EvidenceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str
    route_id: str
    fact_type: FactType
    status: FactStatus
    raw_value: Any = None
    normalized_value: Any = None
    unit: str | None = None
    source: str
    source_kind: str = "public"
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    valid_until: datetime | None = None
    entity_revision: str | None = None
    source_fingerprint: str | None = None
    notes: list[str] = Field(default_factory=list)

    def is_expired(self, now: datetime | None = None) -> bool:
        if self.valid_until is None:
            return False
        current = now or datetime.now(UTC)
        return current >= self.valid_until

    def citation_id(self) -> str:
        payload = {
            "evidence_id": self.evidence_id,
            "route_id": self.route_id,
            "fact_type": self.fact_type.value,
            "source": self.source,
            "entity_revision": self.entity_revision,
        }
        encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        return f"E-{hashlib.sha256(encoded.encode('utf-8')).hexdigest()[:10]}"

    def with_expiry_status(self, now: datetime | None = None) -> EvidenceRecord:
        if self.status == FactStatus.OK and self.is_expired(now):
            return self.model_copy(update={"status": FactStatus.STALE})
        return self
