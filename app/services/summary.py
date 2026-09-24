from __future__ import annotations

from app.domain.report import CheckStatus, DecisionReport, DecisionStatus


class TemplateSummaryRenderer:
    """阶段 1 的确定性模板摘要；LLM 摘要属于阶段 4，且只能改写不能覆盖验证结论。"""

    def render(self, report: DecisionReport) -> str:
        lines: list[str] = []
        if report.status == DecisionStatus.VERIFIED_MATCHES:
            verified = [c for c in report.candidates if c.hard_pass]
            lines.append(f"在当前候选与证据范围内，找到 {len(verified)} 条满足全部硬约束的路线。")
            for candidate in verified[:3]:
                suffix = f"（软偏好满足度 {candidate.soft_score:.0%}）"
                lines.append(f"推荐：{candidate.title}{suffix}")
                for tradeoff in candidate.tradeoffs[:3]:
                    lines.append(f"  - {tradeoff}")
        elif report.status == DecisionStatus.INSUFFICIENT_EVIDENCE:
            lines.append("部分候选缺少可验证的硬约束证据，本次不做已验证推荐。")
            unknown_routes = [
                c
                for c in report.candidates
                if any(
                    check.status == CheckStatus.UNKNOWN
                    for check in c.checks
                    if check.strength == "hard"
                )
            ]
            for candidate in unknown_routes[:3]:
                missing = [
                    check.constraint
                    for check in candidate.checks
                    if check.status == CheckStatus.UNKNOWN
                ]
                lines.append(f"待核验：{candidate.title}，证据不足项：{', '.join(missing)}")
        else:
            lines.append("当前候选中没有满足全部硬约束的路线，未做任何放宽。")

        if report.warnings:
            lines.append(f"运行提示：{'; '.join(report.warnings)}")
        lines.append("以上结论基于当前证据有效期，出行前请复核最新天气与路况。")
        return "\n".join(lines)
