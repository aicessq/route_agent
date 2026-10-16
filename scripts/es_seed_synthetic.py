"""把确定性合成路线批量灌入本地 Elasticsearch 索引（规模测评数据通道）。

用法：
    .venv/bin/python scripts/es_seed_synthetic.py --count 10000
    .venv/bin/python scripts/es_seed_synthetic.py --count 1000 --seed smoke-1 --no-recreate

设计约束：
- 默认 DELETE + CREATE 重建索引：本索引只承载可再生的合成数据，重建保证幂等；
  索引里如有非合成数据时必须显式 --no-recreate。
- location 映射为 text + raw keyword 子字段：查询走 match + operator=and
  （见 app/providers/elasticsearch.py 的 build_search_body），支持城市级与
  区县级召回；若用默认分词的 OR match 会把全城路线都召回，禁止。
- dynamic=strict 拒绝未知字段，防止生成器与索引 schema 漂移。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from eval.synthetic import generate_routes, summarize  # noqa: E402

# 字段与 app/providers/elasticsearch.py 的 _candidate/_DOC_FACT_FIELDS 读取口径一一对应。
# location 用 text（标准分词）+ raw keyword 子字段：查询模板用 match + operator=and，
# 城市级与区县级地点都能召回；raw 供精确聚合/诊断用。
INDEX_MAPPING = {
    "settings": {"number_of_shards": 1, "number_of_replicas": 0},
    "mappings": {
        "dynamic": "strict",
        "properties": {
            "route_id": {"type": "keyword"},
            "title": {"type": "text"},
            "location": {
                "type": "text",
                "fields": {"raw": {"type": "keyword"}},
            },
            "source_kind": {"type": "keyword"},
            "entity_revision": {"type": "keyword"},
            "source_fingerprint": {"type": "keyword"},
            "distance_km": {"type": "float"},
            "elevation_gain_m": {"type": "integer"},
            "duration_minutes": {"type": "integer"},
            "rating": {"type": "float"},
            "tags": {"type": "keyword"},
            "risk_types": {"type": "keyword"},
            "facilities": {"type": "keyword"},
            "generator_version": {"type": "keyword"},
        },
    },
}

BULK_BATCH = 500


def seed(
    client: httpx.Client,
    *,
    index: str,
    routes: list[dict[str, object]],
    recreate: bool,
) -> int:
    """建索引并 bulk 写入；返回最终文档数。"""
    if recreate:
        # 404 = 索引不存在，属正常首次执行。
        response = client.delete(f"/{index}")
        if response.status_code not in (200, 404):
            response.raise_for_status()
    response = client.put(f"/{index}", json=INDEX_MAPPING)
    if response.status_code == 400:
        # 容忍“索引已存在”（--no-recreate 复用已有索引）；其余 400 一律报错。
        error = response.json().get("error", {})
        if error.get("type") != "resource_already_exists_exception":
            response.raise_for_status()
    else:
        response.raise_for_status()

    for start in range(0, len(routes), BULK_BATCH):
        batch = routes[start : start + BULK_BATCH]
        lines: list[str] = []
        for route in batch:
            lines.append(json.dumps({"index": {"_index": index, "_id": route["route_id"]}}))
            lines.append(json.dumps(route, ensure_ascii=False))
        body = ("\n".join(lines) + "\n").encode("utf-8")
        response = client.post(
            "/_bulk", content=body, headers={"Content-Type": "application/x-ndjson"}
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("errors"):
            failures = [
                item["index"].get("error")
                for item in payload["items"]
                if item["index"].get("error")
            ]
            raise RuntimeError(f"bulk 写入失败 {len(failures)} 条: {failures[:3]}")

    client.post(f"/{index}/_refresh")
    count_response = client.get(f"/{index}/_count")
    count_response.raise_for_status()
    return int(count_response.json()["count"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=10000)
    parser.add_argument("--seed", default="synthetic-1")
    parser.add_argument(
        "--url",
        default=os.environ.get("ELASTICSEARCH_URL", "http://127.0.0.1:9201"),
    )
    parser.add_argument("--index", default="route_agent_routes")
    parser.add_argument("--recreate", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument(
        "--out",
        default="data/generated/es_synthetic.ndjson",
        help="可选落盘路径；data/generated/ 已在 .gitignore 中",
    )
    args = parser.parse_args()

    routes = generate_routes(args.count, seed=args.seed)
    stats = summarize(routes)
    print(f"生成 {len(routes)} 条（seed={args.seed}）：")
    missing_labels = [
        ("距离", stats["missing_distance"]),
        ("爬升", stats["missing_elevation"]),
        ("评分", stats["missing_rating"]),
        ("风险证据", stats["missing_risk_evidence"]),
        ("设施证据", stats["missing_facility_evidence"]),
    ]
    summary = " / ".join(f"{label} {count / len(routes):.1%}" for label, count in missing_labels)
    print(f"  缺失率: {summary}")

    out_path = PROJECT_ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in routes), encoding="utf-8"
    )
    print(f"  ndjson 落盘: {out_path}")

    with httpx.Client(base_url=args.url, timeout=30.0) as client:
        total = seed(client, index=args.index, routes=routes, recreate=args.recreate)
    print(f"ES 索引 {args.index} 就绪：{total} 条文档")


if __name__ == "__main__":
    main()
