"""摘要渲染：LLM 可选通道 + 确定性模板兜底 + 报告外结论门禁。"""

from __future__ import annotations

from app.domain.report import DecisionReport
from app.services.llm.base import LLMFailure, extract_json_object
from app.services.llm.gateway import LLMClient
from app.services.llm.prompts import SUMMARY_SYSTEM, SUMMARY_USER_TEMPLATE
from app.services.summary import TemplateSummaryRenderer

_MAX_SUMMARY_CHARS = 600


class SummaryRenderer:
    """优先 LLM 摘要；任何失败或越权都回落模板，永不阻塞报告交付。"""

    def __init__(
        self,
        client: LLMClient | None = None,
        *,
        model: str | None = None,
        template: TemplateSummaryRenderer | None = None,
    ) -> None:
        self.client = client
        self.model = model
        self.template = template or TemplateSummaryRenderer()

    async def render(self, report: DecisionReport) -> tuple[str, str]:
        """返回 (summary, channel)。"""
        if self.client is None:
            return self.template.render(report), "template"
        try:
            summary = await self._render_via_llm(report)
        except LLMFailure:
            return self.template.render(report), "template"
        if summary is None:
            return self.template.render(report), "template"
        return summary, "llm"

    async def _render_via_llm(self, report: DecisionReport) -> str | None:
        report_json = report.model_dump_json(exclude={"candidates": {"__all__": {"evidence"}}})
        result = await self.client.chat_json(
            system=SUMMARY_SYSTEM,
            user=SUMMARY_USER_TEMPLATE.format(report_json=report_json),
            model=self.model,
            purpose="summary",
        )
        payload = extract_json_object(result.content)
        summary = str(payload.get("summary", "")).strip()
        if not summary or len(summary) > _MAX_SUMMARY_CHARS:
            return None
        # 报告外结论门禁：摘要提到的路线标题必须来自报告候选。
        titles = [candidate.title for candidate in report.candidates]
        mentioned = [title for title in titles if title and title in summary]
        if report.verified_route_ids and not mentioned:
            return None
        return summary
