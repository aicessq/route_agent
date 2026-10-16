from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from app.domain.evidence import EvidenceRecord, FactStatus, FactType
from app.domain.intent import RouteIntent
from app.domain.policies import FACT_POLICIES
from app.domain.routes import RouteCandidate
from app.providers.contracts import EvidenceProvider, SearchProvider
from app.providers.location import normalize_location

# 索引文档字段 → 事实类型的固定映射；新增事实类型必须同步这里与灌库脚本 mapping。
_DOC_FACT_FIELDS: dict[FactType, str] = {
    FactType.DISTANCE: "distance_km",
    FactType.ELEVATION_GAIN: "elevation_gain_m",
    FactType.DURATION: "duration_minutes",
    FactType.RISK_TYPES: "risk_types",
    FactType.FACILITIES: "facilities",
    FactType.RATING: "rating",
}


def _cap_or_missing(field: str, cap: float) -> dict[str, Any]:
    """阈值过滤的宽松形态：超阈值候选淘汰，缺字段候选保留。

    缺数据候选必须进入验证层判 UNKNOWN，而不是在检索层被无声吞掉
    （“没查到 ≠ 不满足”是本项目的硬原则）。
    """
    return {
        "bool": {
            "should": [
                {"range": {field: {"lte": cap}}},
                {"bool": {"must_not": {"exists": {"field": field}}}},
            ],
            "minimum_should_match": 1,
        }
    }


def build_search_body(intent: RouteIntent, *, limit: int) -> dict[str, Any]:
    """服务端查询模板（纯函数，离线契约测试直接喂 intent 断言）。

    语义契约：
    - location 为分词字段，match + operator=and：城市级（“北京”）与区县级
      （“延庆”/“北京延庆”）都能召回，且单字 AND 不会把其他区县误召回；
    - 意图地点先经 normalize_location 归一化（去 ·/行政后缀）；
    - 评分降序、缺评分排末尾；size 受检索页上限保护。
    """
    filters: list[dict[str, Any]] = []
    normalized = normalize_location(intent.location)
    if normalized:
        filters.append({"match": {"location": {"query": normalized, "operator": "and"}}})
    if intent.max_distance_km is not None:
        filters.append(_cap_or_missing("distance_km", intent.max_distance_km))
    if intent.max_elevation_gain_m is not None:
        filters.append(_cap_or_missing("elevation_gain_m", intent.max_elevation_gain_m))
    return {
        "size": min(limit, 50),
        "query": {"bool": {"filter": filters}},
        "sort": [{"rating": {"order": "desc", "missing": "_last"}}],
    }


def evidence_from_docs(
    docs: list[dict[str, Any]],
    *,
    source: str,
    fact_types: list[FactType] | None = None,
    now: datetime | None = None,
) -> list[EvidenceRecord]:
    """把 _mget 命中文档映射为证据记录（纯函数，离线可测）。

    原型简化（阶段 2 替换点）：索引即证据源——observed_at 取查询时间、
    valid_until 置空（索引快照无过期语义），动态过期/冲突由 Fake 注入场景
    与批量 Evidence Gateway 覆盖。字段缺失 → UNAVAILABLE，交验证层判 UNKNOWN。
    """
    observed_at = now or datetime.now(UTC)
    records: list[EvidenceRecord] = []
    for doc in docs:
        route_id = str(doc.get("route_id", doc.get("_id", "")))
        for fact_type, field in _DOC_FACT_FIELDS.items():
            if fact_types is not None and fact_type not in fact_types:
                continue
            value = doc.get(field)
            status = FactStatus.OK if value is not None else FactStatus.UNAVAILABLE
            records.append(
                EvidenceRecord(
                    evidence_id=f"{route_id}:{fact_type.value}:es",
                    route_id=route_id,
                    fact_type=fact_type,
                    status=status,
                    raw_value=value,
                    normalized_value=value,
                    unit=FACT_POLICIES[fact_type].unit,
                    source=source,
                    source_kind=doc.get("source_kind", "public_track"),
                    observed_at=observed_at,
                    valid_until=None,
                    entity_revision=doc.get("entity_revision"),
                    source_fingerprint=doc.get("source_fingerprint"),
                )
            )
    return records


class ElasticsearchRouteProvider(SearchProvider, EvidenceProvider):
    """ES 适配器：只发送服务端生成的查询模板，不接受模型生成 DSL。

    search 走 _search；collect 走分批 _mget（批上限由构造参数控制，
    对齐 config/app.yaml 的 evidence_batch_size）。两个纯函数
    （build_search_body / evidence_from_docs）承担全部语义，便于离线契约测试。
    """

    def __init__(
        self,
        base_url: str,
        index_name: str,
        timeout_seconds: float = 2.0,
        *,
        batch_size: int = 20,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.index_name = index_name
        self.timeout = httpx.Timeout(timeout_seconds)
        self.batch_size = batch_size
        # 与 FakeRouteProvider 对齐的调用计数，供报告 budget 读取。
        self.search_calls = 0
        self.evidence_calls = 0

    async def search(self, intent: RouteIntent, *, limit: int) -> list[RouteCandidate]:
        self.search_calls += 1
        body = build_search_body(intent, limit=limit)
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(f"{self.base_url}/{self.index_name}/_search", json=body)
            response.raise_for_status()
            payload = response.json()
        return [self._candidate(hit) for hit in payload.get("hits", {}).get("hits", [])]

    async def collect(
        self,
        route_ids: list[str],
        fact_types: list[FactType],
    ) -> list[EvidenceRecord]:
        self.evidence_calls += 1
        docs: list[dict[str, Any]] = []
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for start in range(0, len(route_ids), self.batch_size):
                batch = list(route_ids)[start : start + self.batch_size]
                response = await client.post(
                    f"{self.base_url}/{self.index_name}/_mget", json={"ids": batch}
                )
                response.raise_for_status()
                for hit in response.json().get("docs", []):
                    # found=false（检索后文档被删）跳过：验证层对无证据事实判 UNKNOWN。
                    if hit.get("found", False):
                        docs.append(hit.get("_source", {}))
        return evidence_from_docs(
            docs,
            source=f"elasticsearch:{self.index_name}",
            fact_types=list(fact_types),
        )

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
