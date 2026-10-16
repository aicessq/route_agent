from __future__ import annotations

import pytest

from eval.synthetic import GENERATOR_VERSION, LOCATIONS, generate_routes, summarize


def test_same_seed_generates_identical_routes() -> None:
    # 同参数必须逐字段一致：规模测评的可复现性依赖这一点。
    assert generate_routes(500) == generate_routes(500)
    assert generate_routes(500, seed="a") == generate_routes(500, seed="a")


def test_different_seed_diverges() -> None:
    assert generate_routes(500) != generate_routes(500, seed="other-seed")


def test_count_and_uniqueness() -> None:
    routes = generate_routes(1000)
    assert len(routes) == 1000
    ids = [r["route_id"] for r in routes]
    assert len(set(ids)) == len(ids)


def test_synthetic_marking_is_enforced() -> None:
    # 硬规则：合成数据不得冒充真实轨迹，三重标记缺一不可。
    routes = generate_routes(300)
    assert all(r["source_kind"] == "synthetic_fixture" for r in routes)
    assert all(str(r["route_id"]).startswith("syn-") for r in routes)
    assert all(str(r["source_fingerprint"]).startswith("fixture-synthetic-") for r in routes)
    assert all(r["generator_version"] == GENERATOR_VERSION for r in routes)


def test_field_ranges_and_locations() -> None:
    routes = generate_routes(1000)
    assert all(r["location"] in LOCATIONS for r in routes)
    for route in routes:
        distance = route["distance_km"]
        if distance is not None:
            assert 2.0 <= distance <= 25.9
        elevation = route["elevation_gain_m"]
        if elevation is not None:
            assert 40 <= elevation <= 1239
        rating = route["rating"]
        if rating is not None:
            assert 3.0 <= rating <= 5.0
        assert route["duration_minutes"] >= 60


def test_missing_rates_stay_near_design_targets() -> None:
    # 缺失率是设计参数（用于刻画 range 过滤静默丢候选的行为），允许统计抖动。
    stats = summarize(generate_routes(4000))
    total = stats["total"]
    assert 0.05 <= stats["missing_distance"] / total <= 0.11
    assert 0.05 <= stats["missing_elevation"] / total <= 0.11
    assert 0.02 <= stats["missing_rating"] / total <= 0.08
    assert 0.22 <= stats["missing_risk_evidence"] / total <= 0.29
    assert 0.15 <= stats["missing_facility_evidence"] / total <= 0.23


def test_evidence_fields_use_domain_vocab() -> None:
    # 风险/设施取值必须来自领域枚举，避免下游验证层遇到未知词汇。
    risk_vocab = {
        "risk_cliff_exposure",
        "risk_river_crossing",
        "risk_road_closure",
        "risk_landslide",
        "risk_ice",
    }
    facility_vocab = {"water_source", "store", "restroom", "camp"}
    routes = generate_routes(1000)
    for route in routes:
        risks = route["risk_types"]
        if risks is not None:
            assert set(risks) <= risk_vocab
        facilities = route["facilities"]
        if facilities is not None:
            assert set(facilities) <= facility_vocab


def test_empty_and_negative_counts() -> None:
    assert generate_routes(0) == []
    assert summarize([]) == {"total": 0}
    with pytest.raises(ValueError):
        generate_routes(-1)


def test_summarize_reports_city_distribution() -> None:
    stats = summarize(generate_routes(2000))
    assert stats["locations"] == len(LOCATIONS)
    assert sum(stats["by_city"].values()) == stats["total"]
