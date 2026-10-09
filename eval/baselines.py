"""三组基线对照的公共实现：A 固定筛选、B 模拟典型 ReAct。

诚声明：基线 B 是确定性模拟（“取证只覆盖前 2 个候选”的典型 ReAct 行为），
用于刻画结构性缺陷（无证据完备性保证）；其结论不依赖具体模型质量。
基线 C（本方案）由编排器真实执行。
"""

from __future__ import annotations

from app.domain.evidence import FactType
from app.domain.intent import RouteIntent
from app.providers.fake import FakeRouteProvider
from app.services.intent_compiler import extract_by_rules
from app.services.llm.fake import FakeLLMClient


class FixedFilterBaseline:
    """基线 A：传统结构化筛选——正则提数 + 索引字段过滤。

    结构性局限（这是对照要刻画的，不是实现 bug）：
    - 索引没有风险数据 → 风险约束完全无法过滤；
    - 索引字段缺失的候选被静默丢弃（不提示 UNKNOWN）。
    """

    def __init__(self, provider: FakeRouteProvider) -> None:
        self.provider = provider

    def run(self, query: str) -> dict:
        intent = extract_by_rules(query)
        candidates = [c for c in self.provider.candidates if self._location_match(intent, c)]
        recommended, dropped_missing_data = [], []
        for candidate in candidates:
            distance = candidate.indexed_distance_km
            elevation = candidate.indexed_elevation_gain_m
            if intent.max_distance_km is not None and distance is None:
                dropped_missing_data.append(candidate.route_id)
                continue
            if intent.max_elevation_gain_m is not None and elevation is None:
                dropped_missing_data.append(candidate.route_id)
                continue
            if intent.max_distance_km is not None and distance > intent.max_distance_km:
                continue
            if intent.max_elevation_gain_m is not None and elevation > intent.max_elevation_gain_m:
                continue
            recommended.append(candidate.route_id)
        return {
            "recommended": recommended,
            "dropped_missing_data": dropped_missing_data,
            "intent": intent.as_public_dict(),
            "provider_calls": 0,
            "llm_calls": 0,
        }

    @staticmethod
    def _location_match(intent: RouteIntent, candidate) -> bool:  # noqa: ANN001
        if not intent.location:
            return True
        return intent.location.casefold() in candidate.location.casefold()


class TypicalReActBaseline:
    """基线 B：模拟典型 ReAct Agent——自由决定取证范围，无完备性保证。

    模拟行为（确定性）：
    1. 搜索候选（LLM 决定调用 search）；
    2. 只对前 2 个候选取证（模型常见的“省调用”倾向）；
    3. 输出推荐：已核验且通过的 + 未核验的（“没查到问题就当没问题”）。
    """

    def __init__(self, provider: FakeRouteProvider, *, evidence_top: int = 2) -> None:
        self.provider = provider
        self.evidence_top = evidence_top

    def run(self, query: str) -> dict:
        intent = extract_by_rules(query)
        candidates = [
            c
            for c in self.provider.candidates
            if not intent.location or intent.location.casefold() in c.location.casefold()
        ]
        checked = candidates[: self.evidence_top]
        unchecked = candidates[self.evidence_top :]

        # 模拟模型只对已核验候选做判断（风险 FAIL 时排除），未核验的全部保留。
        recommended = []
        for candidate in checked:
            risk_record = self.provider.records.get((candidate.route_id, FactType.RISK_TYPES))
            if intent.avoid_risk_types and risk_record is not None:
                risks = set(risk_record.normalized_value or [])
                if risks.intersection({r.value for r in intent.avoid_risk_types}):
                    continue
            if (
                intent.max_distance_km is not None
                and candidate.indexed_distance_km is not None
                and candidate.indexed_distance_km > intent.max_distance_km
            ):
                continue
            recommended.append(candidate.route_id)
        # 结构性缺陷：未核验候选被无差别推荐。
        recommended.extend(candidate.route_id for candidate in unchecked)

        return {
            "recommended": recommended,
            "unchecked_recommended": [candidate.route_id for candidate in unchecked],
            "checked_ids": [candidate.route_id for candidate in checked],
            "intent": intent.as_public_dict(),
            "provider_calls": 2,  # search + evidence（一次批量）
            "llm_calls": 3,  # 规划 search + 规划 evidence + 总结
        }


def fixture_ground_truth(provider: FakeRouteProvider) -> dict:
    """从 fixture 证据构造地面真值：每条路线的风险集合与数据可用性。"""
    truth: dict[str, dict] = {}
    for (route_id, fact_type), record in provider.records.items():
        entry = truth.setdefault(
            route_id, {"risks": set(), "risk_available": True, "distance": None}
        )
        if fact_type == FactType.RISK_TYPES:
            if record.status.value in {"UNAVAILABLE", "NOT_FOUND", "CONFLICT"}:
                entry["risk_available"] = False
            else:
                entry["risks"] = set(record.normalized_value or [])
        elif fact_type == FactType.DISTANCE:
            entry["distance"] = record.normalized_value
    return truth


def count_hard_mispass(
    recommended: list[str],
    intent: RouteIntent,
    ground_truth: dict,
) -> dict:
    """误通过统计：违反硬约束的推荐 + 风险证据不可用仍被推荐的（未验证推荐）。"""
    violations: list[str] = []
    unverified: list[str] = []
    for route_id in recommended:
        entry = ground_truth.get(route_id)
        if entry is None:
            continue
        risky = entry["risks"].intersection({r.value for r in intent.avoid_risk_types})
        if intent.avoid_risk_types and entry["risk_available"] and risky:
            violations.append(route_id)
            continue
        if intent.avoid_risk_types and not entry["risk_available"]:
            unverified.append(route_id)
    return {
        "violations": violations,
        "unverified_recommended": unverified,
        "total": len(violations) + len(unverified),
    }


def build_fake_llm_for_scripts(scripted_intents: list[dict]) -> FakeLLMClient:
    """按脚本顺序产出意图 JSON（编译通道的确定性替身）。"""
    return FakeLLMClient(scripted_intents, model="fake-small")
