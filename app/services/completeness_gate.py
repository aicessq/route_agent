"""规则驱动的 Completeness Gate：只有缺失字段会阻塞后续确定性步骤时才追问。

追问不是模型自主行为：每个问题必须对应一个具体阻塞字段，且每个 Run 最多 2 轮。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.intent import RouteIntent


@dataclass(slots=True)
class GateQuestion:
    """一个待用户补充的字段：field 是合并进 Intent 的键。"""

    field: str
    text: str
    kind: str = "missing_field"  # missing_field / missing_constraints / relaxation_consent


class CompletenessGate:
    def check(self, intent: RouteIntent) -> GateQuestion | None:
        # 规则 1：无地点无法召回候选，必须追问。
        if not intent.location or not intent.location.strip():
            return GateQuestion(
                field="location",
                text="请问您希望在哪个城市或地区周边徒步？",
            )
        # 规则 2：无任何可验证约束时不做“已验证推荐”，追问至少一项约束。
        if not intent.required_fact_fields():
            return GateQuestion(
                field="constraints",
                text="请补充至少一项可验证条件（如距离上限、累计爬升上限或需避开的风险类型）。",
                kind="missing_constraints",
            )
        return None
