from __future__ import annotations

from typing import Any

import httpx

from app.domain.evidence import EvidenceRecord, FactType
from app.domain.intent import RouteIntent
from app.domain.routes import RouteCandidate
from app.providers.contracts import EvidenceProvider, SearchProvider


class ElasticsearchRouteProvider(SearchProvider, EvidenceProvider):
    """本地 ES 适配器：只发送服务端生成的查询模板，不接受模型生成 DSL。"""

    def __init__(self, base_url: str, index_name: str, timeout_seconds: float = 2.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.index_name = index_name
        self.timeout = httpx.Timeout(timeout_seconds)

    async def search(self, intent: RouteIntent, *, limit: int) -> list[RouteCandidate]:
        filters: list[dict[str, Any]] = [{"match": {"location": intent.location}}]
        if intent.max_distance_km is not None:
            filters.append({"range": {"distance_km": {"lte": intent.max_distance_km}}})
        if intent.max_elevation_gain_m is not None:
            filters.append({"range": {"elevation_gain_m": {"lte": intent.max_elevation_gain_m}}})
        query = {
            "size": min(limit, 50),
            "query": {"bool": {"filter": filters}},
            "sort": [{"rating": {"order": "desc", "missing": "_last"}}],
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/{self.index_name}/_search",
                json=query,
            )
            response.raise_for_status()
            payload = response.json()
        return [self._candidate(hit) for hit in payload.get("hits", {}).get("hits", [])]

    async def collect(
        self,
        route_ids: list[str],
        fact_types: list[FactType],
    ) -> list[EvidenceRecord]:
        # 初始化阶段先保留契约；批量 Evidence Gateway 将在阶段 2 接入。
        raise NotImplementedError("Evidence Gateway is scheduled for phase 2")

    @staticmethod
    def _candidate(hit: dict[str, Any]) -> RouteCandidate:
        source = hit.get("_source", {})
        return RouteCandidate.model_validate(
            {
                "route_id": str(source.get("route_id", hit.get("_id"))),
                "title": source.get("title", "未命名路线"),
                "location": source.get("location", "未知地点"),
                "source_kind": source.get("source_kind", "public_track"),
                "entity_revision": source.get("entity_revision", "unknown"),
                "source_fingerprint": source.get("source_fingerprint", "unknown"),
                "indexed_distance_km": source.get("distance_km"),
                "indexed_elevation_gain_m": source.get("elevation_gain_m"),
                "tags": source.get("tags", []),
            }
        )
