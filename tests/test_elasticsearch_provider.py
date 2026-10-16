from __future__ import annotations

from app.domain.evidence import FactStatus, FactType
from app.domain.intent import RouteIntent
from app.providers.elasticsearch import build_search_body, evidence_from_docs
from app.providers.location import normalize_location


def _intent(**overrides) -> RouteIntent:
    payload = {
        "location": "北京·延庆",
        "max_distance_km": 8,
        "max_elevation_gain_m": 300,
        "avoid_risk_types": ["risk_cliff_exposure"],
    }
    payload.update(overrides)
    return RouteIntent.model_validate(payload)


def test_search_body_location_uses_and_match() -> None:
    # 单字 OR 匹配会把全城路线都召回；必须显式 operator=and。
    body = build_search_body(_intent(), limit=10)
    location_filter = body["query"]["bool"]["filter"][0]
    match = location_filter["match"]["location"]
    assert match["operator"] == "and"
    assert match["query"] == "北京延庆"


def test_search_body_keeps_missing_field_candidates() -> None:
    # 缺索引字段候选须保留进验证层（UNKNOWN），只有超阈值才在检索层淘汰。
    body = build_search_body(_intent(), limit=10)
    distance_filter = body["query"]["bool"]["filter"][1]["bool"]
    assert distance_filter["minimum_should_match"] == 1
    shoulds = distance_filter["should"]
    assert any("range" in clause for clause in shoulds)
    # 缺字段容忍子句的 must_not 嵌套在 bool 内。
    assert any("must_not" in clause.get("bool", {}) for clause in shoulds)


def test_search_body_without_constraints() -> None:
    # 无地点、无阈值：不放假过滤条件；size 受页上限保护。
    body = build_search_body(RouteIntent(), limit=99)
    assert body["query"]["bool"]["filter"] == []
    assert body["size"] == 50


def test_normalize_location_strips_admin_suffixes() -> None:
    assert normalize_location("北京延庆县") == "北京延庆"
    assert normalize_location("延庆周边") == "延庆"
    assert normalize_location("北京·延庆") == "北京延庆"
    assert normalize_location("") == ""


def test_evidence_from_docs_maps_ok_and_unavailable() -> None:
    docs = [
        {
            "route_id": "syn-000001",
            "source_kind": "synthetic_fixture",
            "entity_revision": "syn-000001-r1",
            "source_fingerprint": "fixture-synthetic-abc",
            "distance_km": 8.6,
            "elevation_gain_m": 599,
            "risk_types": None,
            "facilities": ["store"],
        }
    ]
    records = evidence_from_docs(
        docs,
        source="elasticsearch:route_agent_routes",
        fact_types=[FactType.DISTANCE, FactType.RISK_TYPES, FactType.FACILITIES],
    )
    by_type = {record.fact_type: record for record in records}
    distance = by_type[FactType.DISTANCE]
    assert distance.status == FactStatus.OK
    assert distance.normalized_value == 8.6
    assert distance.unit == "km"
    assert distance.entity_revision == "syn-000001-r1"
    assert distance.source_fingerprint == "fixture-synthetic-abc"
    assert distance.source_kind == "synthetic_fixture"
    # 风险字段缺失 → UNAVAILABLE，交由验证层判 UNKNOWN。
    assert by_type[FactType.RISK_TYPES].status == FactStatus.UNAVAILABLE
    assert by_type[FactType.FACILITIES].status == FactStatus.OK
    # 索引快照型证据无过期语义。
    assert all(record.valid_until is None for record in records)


def test_evidence_from_docs_filters_requested_fact_types() -> None:
    docs = [
        {
            "route_id": "syn-000002",
            "source_kind": "synthetic_fixture",
            "distance_km": 5.0,
            "rating": 4.1,
        }
    ]
    records = evidence_from_docs(docs, source="es", fact_types=[FactType.RATING])
    assert [record.fact_type for record in records] == [FactType.RATING]
    assert records[0].status == FactStatus.OK


def test_evidence_from_docs_marks_all_missing_when_doc_empty() -> None:
    records = evidence_from_docs([{}], source="es", fact_types=None)
    assert records
    assert all(record.status == FactStatus.UNAVAILABLE for record in records)
    # evidence_id 以 route_id:fact_type:es 形式保持稳定（citation_id 依赖它）。
    assert records[0].route_id == ""
    assert records[0].evidence_id == ":distance_km:es"
