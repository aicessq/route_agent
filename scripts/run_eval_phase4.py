"""阶段 4 测评：Golden NL Set + 三组基线对照。

用法：
    python scripts/run_eval_phase4.py            # FakeLLM 确定性模式（CI 安全）
    python scripts/run_eval_phase4.py --real     # Agnes 真实模型编译（需 secrets/llm.env）

核心门槛：基线 C（本方案）硬约束误通过总数必须为 0。
基线 A/B 为确定性模拟，用于刻画结构性差异；--real 只影响 C 的意图编译通道。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "eval"))

from baselines import (  # noqa: E402
    FixedFilterBaseline,
    TypicalReActBaseline,
    count_hard_mispass,
    fixture_ground_truth,
)

from app.domain.intent import RouteIntent  # noqa: E402
from app.providers.fake import FakeRouteProvider  # noqa: E402
from app.runtime.local_store import LocalDecision  # noqa: E402
from app.runtime.state import RunStatus  # noqa: E402
from app.services.decision_orchestrator import DecisionOrchestrator  # noqa: E402
from app.services.decision_service import DecisionService  # noqa: E402
from app.services.llm.fake import FakeLLMClient  # noqa: E402
from app.settings import get_settings  # noqa: E402


def _run_baseline_c(spec: dict, *, real: bool) -> dict:
    provider = FakeRouteProvider()
    service = DecisionService(provider, provider)
    llm_client = None
    primary_model = None
    if real:
        from app.services.llm.gateway import OpenAICompatGateway

        settings = get_settings()
        if settings.llm_api_key and settings.llm_base_url:
            llm_client = OpenAICompatGateway(
                base_url=settings.llm_base_url,
                api_key=settings.llm_api_key,
                default_model=settings.llm_model,
                timeout_seconds=settings.llm_timeout_seconds,
            )
            primary_model = settings.llm_model
    elif "scripted_intent" in spec:
        llm_client = FakeLLMClient([spec["scripted_intent"]], model="fake-small")
        primary_model = "fake-small"

    orchestrator = DecisionOrchestrator(
        service,
        llm_client=llm_client,
        primary_model=primary_model,
        max_repair_rounds=2,
    )
    item = LocalDecision(
        decision_id=f"eval-{spec['query_id']}",
        request_fingerprint="f",
        query=spec["query"],
        intent=None,
        candidate_limit=10,
        user_key="eval",
    )
    asyncio.run(orchestrator.run(item))

    # 门禁追问场景：用期望答案恢复后重跑（模拟 HITL 完整闭环）。
    if item.status == RunStatus.WAITING_USER and spec.get("expected_gate"):
        answer = spec["expected_gate"]["answer"]
        merged = item.intent.model_dump(mode="json")
        merged.update(answer)
        merged.pop("schema_version", None)
        item.intent = RouteIntent.model_validate(merged)
        item.intent_version += 1
        item.question = None
        item.status = RunStatus.QUEUED
        asyncio.run(orchestrator.run(item))

    report = item.report
    return {
        "status": item.status.value,
        "recommended": report.verified_route_ids if report else [],
        "relaxation_applied": len(item.relaxation_applied) > 0,
        "relaxation_records": [
            {"field": record.field, "from_value": record.from_value, "to_value": record.to_value}
            for record in item.relaxation_applied
        ],
        "intent": item.intent.as_public_dict() if item.intent else None,
        "compile_source": item.compile_source,
        "llm_tokens": item.cost.total_tokens,
        "llm_calls": item.cost.call_count,
        "provider_calls": (report.budget.get("provider_calls") if report else None),
    }


def _location_equivalent(expected: str, actual) -> bool:
    """地点按归一化语义比较：相等或一方为另一方的更具体/更宽泛写法。"""
    from app.providers.fake import _normalize_location

    if actual is None:
        return False
    left = _normalize_location(str(expected))
    right = _normalize_location(str(actual))
    if left == right:
        return True
    return len(left) >= 2 and len(right) >= 2 and (left in right or right in left)


def _intent_field_accuracy(compiled: dict | None, expected: dict) -> tuple[int, int, list]:
    """字段级编译准确率：返回 (命中数, 总核对数, 差异明细)。"""
    if compiled is None:
        return (
            0,
            len(expected),
            [{"field": k, "expected": v, "actual": None} for k, v in expected.items()],
        )
    hits, total = 0, 0
    mismatches: list = []
    for field, value in expected.items():
        total += 1
        actual = compiled.get(field)
        if isinstance(value, list):
            if actual is not None and set(actual) == set(value):
                hits += 1
            else:
                mismatches.append({"field": field, "expected": value, "actual": actual})
        elif field == "location" and _location_equivalent(str(value), actual) or actual == value:
            hits += 1
        else:
            mismatches.append({"field": field, "expected": value, "actual": actual})
    return hits, total, mismatches


def main() -> int:
    parser = argparse.ArgumentParser(description="阶段 4 三组基线对照")
    parser.add_argument("--real", action="store_true", help="真实 Agnes 模型编译（默认 FakeLLM）")
    args = parser.parse_args()

    golden = json.loads((PROJECT_ROOT / "eval/golden_nl_queries.json").read_text(encoding="utf-8"))
    provider = FakeRouteProvider()
    ground_truth = fixture_ground_truth(provider)
    baseline_a = FixedFilterBaseline(provider)
    baseline_b = TypicalReActBaseline(provider)

    results = []
    a_mispass_total = b_mispass_total = c_mispass_total = 0
    c_intent_hits, c_intent_total = 0, 0
    c_status_agree = 0
    c_llm_tokens = c_llm_calls = 0

    for spec in golden["queries"]:
        if args.real:
            time.sleep(1.5)  # 对真实服务保持礼貌间隔，避免限流污染测量
        intent_for_truth = RouteIntent.model_validate(spec["scripted_intent"])

        result_a = baseline_a.run(spec["query"])
        result_b = baseline_b.run(spec["query"])
        result_c = _run_baseline_c(spec, real=args.real)

        mispass_a = count_hard_mispass(result_a["recommended"], intent_for_truth, ground_truth)
        mispass_b = count_hard_mispass(result_b["recommended"], intent_for_truth, ground_truth)
        c_intent = result_c.get("intent") or {}
        c_check_intent = RouteIntent.model_validate(
            {k: v for k, v in c_intent.items()} if c_intent else {}
        )
        mispass_c = count_hard_mispass(result_c["recommended"], c_check_intent, ground_truth)

        # 编译准确率按放宽前值核对：修复中的授权放宽不属于编译产物。
        accuracy_intent = dict(c_intent)
        for record in result_c.get("relaxation_records") or []:
            accuracy_intent[record["field"]] = record["from_value"]

        a_mispass_total += mispass_a["total"]
        b_mispass_total += mispass_b["total"]
        c_mispass_total += mispass_c["total"]
        c_llm_tokens += result_c.get("llm_tokens") or 0
        c_llm_calls += result_c.get("llm_calls") or 0

        expected_intent = spec.get("expected_intent") or {}
        hits, total, mismatches = _intent_field_accuracy(accuracy_intent, expected_intent)
        c_intent_hits += hits
        c_intent_total += total

        expected_final = spec.get("expected_final") or {}
        if result_c["status"] == expected_final.get("status"):
            c_status_agree += 1
        verified_ok = sorted(result_c["recommended"]) == sorted(
            expected_final.get("verified_route_ids", [])
        )

        results.append(
            {
                "query_id": spec["query_id"],
                "baseline_a": {
                    "recommended": result_a["recommended"],
                    "dropped_missing_data": result_a["dropped_missing_data"],
                    "mispass": mispass_a,
                },
                "baseline_b": {
                    "recommended": result_b["recommended"],
                    "unchecked_recommended": result_b["unchecked_recommended"],
                    "mispass": mispass_b,
                },
                "baseline_c": {
                    "status": result_c["status"],
                    "recommended": result_c["recommended"],
                    "status_agrees": result_c["status"] == expected_final.get("status"),
                    "verified_agrees": verified_ok,
                    "relaxation_applied": result_c["relaxation_applied"],
                    "mispass": mispass_c,
                    "compile_source": result_c["compile_source"],
                    "intent_field_mismatches": mismatches,
                },
            }
        )

    total = len(golden["queries"])
    mode = "real-agnes" if args.real else "fake-llm"
    summary = {
        "spec_version": f"phase4-baselines.{mode}",
        "mode": mode,
        "total_queries": total,
        "baseline_a_fixed_filter": {
            "hard_mispass_total": a_mispass_total,
            "note": "索引无风险数据：风险约束无法过滤；缺数据候选被静默丢弃",
        },
        "baseline_b_react_simulated": {
            "hard_mispass_total": b_mispass_total,
            "note": "确定性模拟：只取证前2个候选，未核验候选被无差别推荐（无完备性保证）",
        },
        "baseline_c_pipeline": {
            "hard_mispass_total": c_mispass_total,
            "status_agreement": c_status_agree,
            "verified_set_agreement": sum(1 for r in results if r["baseline_c"]["verified_agrees"]),
            "intent_field_accuracy": round(c_intent_hits / c_intent_total, 4)
            if c_intent_total
            else None,
            "llm_calls": c_llm_calls,
            "llm_tokens": c_llm_tokens,
        },
        "gate": {
            "c_hard_mispass_must_be_zero": c_mispass_total == 0,
            "passed": c_mispass_total == 0
            and c_status_agree == total
            and all(r["baseline_c"]["verified_agrees"] for r in results),
        },
        "results": results,
    }

    output_dir = PROJECT_ROOT / "eval/results"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / f"phase4-baselines-{mode}.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"阶段 4 三组基线对照（{total} 条 NL 查询，模式：{mode}）")
    print(f"  A 固定筛选   硬约束误通过: {a_mispass_total}")
    print(f"  B ReAct 模拟 硬约束误通过: {b_mispass_total}")
    print(f"  C 本方案     硬约束误通过: {c_mispass_total} (门槛: 必须为 0)")
    print(f"  C 状态一致:   {c_status_agree}/{total}")
    print(f"  C 编译字段准确率: {c_intent_hits}/{c_intent_total}")
    print(f"  C LLM 调用/token: {c_llm_calls} 次 / {c_llm_tokens} tokens")
    for result in results:
        c = result["baseline_c"]
        flag = "PASS" if c["verified_agrees"] and c["mispass"]["total"] == 0 else "FAIL"
        print(
            f"  [{flag}] {result['query_id']}: {c['status']} -> {c['recommended']} "
            f"(compile={c['compile_source'] or 'structured'})"
        )
    print("结果已写入 eval/results/")
    return 0 if summary["gate"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
