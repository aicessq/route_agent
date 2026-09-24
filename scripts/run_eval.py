"""Golden Query 测评：按冻结期望集运行确定性决策管线并输出量化结果。

用法：
    python scripts/run_eval.py [--golden eval/golden_queries.json] [--output eval/results]

核心门槛：hard_constraint_mispass 必须为 0（任何期望不通过的候选被标记 hard_pass 即失败）。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.domain.evidence import FactStatus, FactType  # noqa: E402
from app.domain.intent import RouteIntent  # noqa: E402
from app.providers.fake import FakeRouteProvider  # noqa: E402
from app.services.decision_service import DecisionService  # noqa: E402


def apply_overrides(
    provider: FakeRouteProvider,
    overrides: list[dict],
) -> None:
    for override in overrides:
        key = (override["route_id"], FactType(override["fact_type"]))
        record = provider.records.get(key)
        if record is None:
            raise ValueError(f"override references missing record: {key}")
        updates: dict = {}
        if "status" in override:
            updates["status"] = FactStatus(override["status"])
        if "value" in override:
            updates["raw_value"] = override["value"]
            updates["normalized_value"] = override["value"]
        if "valid_until_offset_seconds" in override:
            offset = override["valid_until_offset_seconds"]
            base = record.valid_until or datetime.now(UTC)
            updates["valid_until"] = base + timedelta(seconds=offset)
        provider.records[key] = record.model_copy(update=updates)


def evaluate_query(spec: dict) -> dict:
    intent = RouteIntent.model_validate(spec["intent"])
    failure = spec.get("provider_failure")
    provider = FakeRouteProvider(
        fail_evidence=failure == "evidence",
        fail_search=failure == "search",
    )
    if not failure:
        # 默认 fixture 已经是目标状态；覆盖只改变指定事实。
        apply_overrides(provider, spec.get("evidence_overrides", []))
    service = DecisionService(provider, provider)

    report = asyncio_decide(service, intent)
    expected = spec["expected"]

    route_results: list[dict] = []
    hard_mispass = 0
    check_mismatches: list[str] = []
    by_route = {candidate.route_id: candidate for candidate in report.candidates}

    for expectation in expected.get("route_expectations", []):
        route_id = expectation["route_id"]
        candidate = by_route.get(route_id)
        if candidate is None:
            route_results.append(
                {
                    "route_id": route_id,
                    "found": False,
                    "expected_hard_pass": expectation["hard_pass"],
                }
            )
            if expectation["hard_pass"]:
                hard_mispass += 1
            continue
        agrees = candidate.hard_pass == expectation["hard_pass"]
        if not agrees and expectation["hard_pass"] is False and candidate.hard_pass:
            hard_mispass += 1
        entry = {
            "route_id": route_id,
            "found": True,
            "expected_hard_pass": expectation["hard_pass"],
            "actual_hard_pass": candidate.hard_pass,
            "hard_pass_agrees": agrees,
        }
        expected_check = expectation.get("check")
        if expected_check:
            actual = next(
                (c for c in candidate.checks if c.constraint == expected_check["constraint"]),
                None,
            )
            check_ok = (
                actual is not None
                and actual.status.value == expected_check["status"]
                and actual.stale == expected_check.get("stale", False)
            )
            entry["check_agrees"] = check_ok
            if not check_ok:
                check_mismatches.append(
                    f"{spec['query_id']}/{route_id}/{expected_check['constraint']}"
                )
        route_results.append(entry)

    status_ok = report.status.value == expected["status"]
    verified_ok = sorted(report.verified_route_ids) == sorted(expected["verified_route_ids"])

    return {
        "query_id": spec["query_id"],
        "status_expected": expected["status"],
        "status_actual": report.status.value,
        "status_agrees": status_ok,
        "verified_agrees": verified_ok,
        "hard_mispass": hard_mispass,
        "check_mismatches": check_mismatches,
        "route_results": route_results,
    }


def asyncio_decide(service: DecisionService, intent: RouteIntent):
    import asyncio

    return asyncio.run(service.decide(intent))


def main() -> int:
    parser = argparse.ArgumentParser(description="Golden Query 测评")
    parser.add_argument("--golden", default="eval/golden_queries.json")
    parser.add_argument("--output", default="eval/results")
    args = parser.parse_args()

    golden_path = PROJECT_ROOT / args.golden
    spec = json.loads(golden_path.read_text(encoding="utf-8"))
    results = [evaluate_query(q) for q in spec["queries"]]

    total = len(results)
    status_agree = sum(1 for r in results if r["status_agrees"])
    verified_agree = sum(1 for r in results if r["verified_agrees"])
    hard_mispass_total = sum(r["hard_mispass"] for r in results)
    check_mismatch_total = sum(len(r["check_mismatches"]) for r in results)

    summary = {
        "spec_version": spec["spec_version"],
        "executed_at": datetime.now(UTC).isoformat(),
        "total_queries": total,
        "status_agreement": round(status_agree / total, 4),
        "verified_set_agreement": round(verified_agree / total, 4),
        "hard_constraint_mispass": hard_mispass_total,
        "check_mismatch": check_mismatch_total,
        "gate": {
            "hard_constraint_mispass_must_be_zero": hard_mispass_total == 0,
            "passed": hard_mispass_total == 0
            and status_agree == total
            and verified_agree == total
            and check_mismatch_total == 0,
        },
        "results": results,
    }

    output_dir = PROJECT_ROOT / args.output
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output_path = output_dir / f"eval-{stamp}.json"
    output_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    # 固定名副本便于 diff 对比两次运行。
    (output_dir / "latest.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"Golden Query 测评：{total} 条")
    print(f"  状态一致率:          {status_agree}/{total}")
    print(f"  已验证集合一致率:    {verified_agree}/{total}")
    print(f"  硬约束误通过总数:    {hard_mispass_total} (门槛: 必须为 0)")
    print(f"  约束级判定不一致:    {check_mismatch_total}")
    for result in results:
        flag = (
            "PASS"
            if (
                result["status_agrees"]
                and result["verified_agrees"]
                and result["hard_mispass"] == 0
                and not result["check_mismatches"]
            )
            else "FAIL"
        )
        print(
            f"  [{flag}] {result['query_id']}: status={result['status_actual']} "
            f"(expected {result['status_expected']})"
        )
    print(f"结果已写入: {output_path}")
    return 0 if summary["gate"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
