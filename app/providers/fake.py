from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime, timedelta

from app.domain.evidence import EvidenceRecord, FactStatus, FactType
from app.domain.intent import FacilityType, RiskType, RouteIntent
from app.domain.routes import RouteCandidate, RouteSourceKind
from app.providers.contracts import EvidenceProvider, SearchProvider
from app.providers.location import normalize_location

# 历史别名：脚本（run_eval_phase4 等）按 _normalize_location 引用，保持兼容。
_normalize_location = normalize_location


class FakeRouteProvider(SearchProvider, EvidenceProvider):
    """不访问网络的确定性 Provider，可通过 records 注入缺失、冲突和故障场景。"""

    def __init__(
        self,
        candidates: Sequence[RouteCandidate] | None = None,
        records: Mapping[tuple[str, FactType], EvidenceRecord] | None = None,
        *,
        delay_seconds: float = 0.0,
        fail_search: bool = False,
        fail_evidence: bool = False,
    ) -> None:
        self.candidates = list(candidates or default_candidates())
        self.records = dict(records or default_evidence(self.candidates))
        self.delay_seconds = delay_seconds
        self.fail_search = fail_search
        self.fail_evidence = fail_evidence
        self.search_calls = 0
        self.evidence_calls = 0

    async def search(self, intent: RouteIntent, *, limit: int) -> list[RouteCandidate]:
        self.search_calls += 1
        await self._delay()
        if self.fail_search:
            raise RuntimeError("fake search provider unavailable")
        # 地点归一化匹配：兼容“北京延庆/延庆县/密云区”等自然写法对“北京·延庆”。
        intent_norm = _normalize_location(intent.location)
        matches = [
            candidate
            for candidate in self.candidates
            if not intent_norm or intent_norm in _normalize_location(candidate.location)
        ]
        return matches[:limit]

    async def collect(
        self,
        route_ids: Sequence[str],
        fact_types: Sequence[FactType],
    ) -> list[EvidenceRecord]:
        self.evidence_calls += 1
        await self._delay()
        if self.fail_evidence:
            raise RuntimeError("fake evidence provider unavailable")
        requested = set(fact_types)
        return [
            record
            for (route_id, fact_type), record in self.records.items()
            if route_id in route_ids and fact_type in requested
        ]

    async def _delay(self) -> None:
        if self.delay_seconds > 0:
            await asyncio.sleep(self.delay_seconds)


def default_candidates() -> list[RouteCandidate]:
    return [
        RouteCandidate(
            route_id="bj-yanqing-001",
            title="妫水河亲子环线",
            location="北京·延庆",
            source_kind=RouteSourceKind.CURATED,
            entity_revision="route-001-r3",
            source_fingerprint="fixture-curated-001",
            indexed_distance_km=7.6,
            indexed_elevation_gain_m=248,
            tags=["亲子", "环线"],
        ),
        RouteCandidate(
            route_id="bj-huairou-002",
            title="怀柔山脊观景线",
            location="北京·怀柔",
            source_kind=RouteSourceKind.PUBLIC_TRACK,
            entity_revision="track-002-r1",
            source_fingerprint="fixture-public-002",
            indexed_distance_km=6.2,
            indexed_elevation_gain_m=180,
            tags=["观景", "山脊"],
        ),
        RouteCandidate(
            route_id="bj-mentougou-003",
            title="门头沟溪谷线",
            location="北京·门头沟",
            source_kind=RouteSourceKind.PUBLIC_TRACK,
            entity_revision="track-003-r2",
            source_fingerprint="fixture-public-003",
            indexed_distance_km=8.5,
            indexed_elevation_gain_m=260,
            tags=["溪谷"],
        ),
        RouteCandidate(
            route_id="bj-miyun-004",
            title="密云林道旧轨迹",
            location="北京·密云",
            source_kind=RouteSourceKind.SYNTHETIC,
            entity_revision="fixture-004-r1",
            source_fingerprint="fixture-synthetic-004",
            indexed_distance_km=None,
            indexed_elevation_gain_m=None,
            tags=["待核验"],
        ),
    ]


def default_evidence(
    candidates: Iterable[RouteCandidate],
) -> dict[tuple[str, FactType], EvidenceRecord]:
    now = datetime.now(UTC)
    values: dict[str, dict[FactType, object]] = {
        "bj-yanqing-001": {
            FactType.DISTANCE: 7.6,
            FactType.ELEVATION_GAIN: 248,
            FactType.DURATION: 118,
            FactType.RISK_TYPES: [],
            FactType.FACILITIES: [FacilityType.WATER_SOURCE.value, FacilityType.STORE.value],
            FactType.RATING: 4.6,
        },
        "bj-huairou-002": {
            FactType.DISTANCE: 6.2,
            FactType.ELEVATION_GAIN: 180,
            FactType.DURATION: 106,
            FactType.RISK_TYPES: [RiskType.CLIFF_EXPOSURE.value],
            FactType.FACILITIES: [FacilityType.WATER_SOURCE.value],
            FactType.RATING: 4.8,
        },
        "bj-mentougou-003": {
            FactType.DISTANCE: 8.5,
            FactType.ELEVATION_GAIN: 260,
            FactType.DURATION: 142,
            FactType.RISK_TYPES: [],
            FactType.FACILITIES: [FacilityType.STORE.value],
            FactType.RATING: 4.2,
        },
        "bj-miyun-004": {
            FactType.DISTANCE: 5.5,
            FactType.ELEVATION_GAIN: 210,
            FactType.DURATION: 100,
            FactType.RISK_TYPES: None,
            FactType.FACILITIES: [FacilityType.CAMP.value],
            FactType.RATING: 3.9,
        },
    }
    result: dict[tuple[str, FactType], EvidenceRecord] = {}
    valid_until = now + timedelta(hours=6)
    for candidate in candidates:
        for fact_type, value in values.get(candidate.route_id, {}).items():
            status = FactStatus.OK if value is not None else FactStatus.UNAVAILABLE
            result[(candidate.route_id, fact_type)] = EvidenceRecord(
                evidence_id=f"{candidate.route_id}:{fact_type.value}:fixture",
                route_id=candidate.route_id,
                fact_type=fact_type,
                status=status,
                raw_value=value,
                normalized_value=value,
                unit={
                    FactType.DISTANCE: "km",
                    FactType.ELEVATION_GAIN: "m",
                    FactType.DURATION: "minutes",
                    FactType.RATING: "stars",
                }.get(fact_type, "enum[]"),
                source="fixture",
                source_kind=candidate.source_kind.value,
                observed_at=now,
                valid_until=valid_until,
                entity_revision=candidate.entity_revision,
                source_fingerprint=candidate.source_fingerprint,
            )
    return result
