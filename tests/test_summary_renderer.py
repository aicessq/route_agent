from __future__ import annotations

from app.domain.report import DecisionStatus
from app.services.llm.fake import FakeLLMClient
from app.services.summary_renderer import SummaryRenderer


def _report(verified: bool = True) -> object:
    from app.domain.report import DecisionReport

    return DecisionReport(
        status=DecisionStatus.VERIFIED_MATCHES if verified else DecisionStatus.NO_MATCH,
        execution_mode="local_async",
        intent_fingerprint="f" * 64,
        policy_version="p",
        verified_route_ids=["bj-yanqing-001"] if verified else [],
        candidates=[
            {
                "route_id": "bj-yanqing-001",
                "title": "妫水河亲子环线",
                "source_kind": "curated_route",
                "checks": [],
                "hard_pass": verified,
                "soft_score": 1.0,
            }
        ],
    )


class TestSummaryRenderer:
    async def test_llm_channel_success(self) -> None:
        client = FakeLLMClient(
            [{"summary": "推荐妫水河亲子环线，满足全部硬约束。以上结论基于当前证据有效期。"}]
        )
        renderer = SummaryRenderer(client, model="m")
        summary, channel = await renderer.render(_report())
        assert channel == "llm"
        assert "妫水河" in summary

    async def test_llm_failure_falls_back_to_template(self) -> None:
        from app.services.llm.base import LLMFailure, LLMFailureKind

        client = FakeLLMClient([LLMFailure(LLMFailureKind.AUTH, "x")])
        renderer = SummaryRenderer(client, model="m")
        summary, channel = await renderer.render(_report())
        assert channel == "template"
        assert "推荐" in summary

    async def test_summary_missing_verified_title_falls_back(self) -> None:
        # 摘要提到报告外的路线标题 → 越权 → 模板兜底。
        client = FakeLLMClient([{"summary": "推荐香山红叶线，非常好走。"}])
        renderer = SummaryRenderer(client, model="m")
        summary, channel = await renderer.render(_report())
        assert channel == "template"

    async def test_overlong_summary_falls_back(self) -> None:
        client = FakeLLMClient([{"summary": "推荐妫水河亲子环线。" + "很长" * 400}])
        renderer = SummaryRenderer(client, model="m")
        _, channel = await renderer.render(_report())
        assert channel == "template"

    async def test_no_client_uses_template(self) -> None:
        renderer = SummaryRenderer(None)
        summary, channel = await renderer.render(_report())
        assert channel == "template"
        assert "推荐" in summary

    async def test_llm_invalid_json_falls_back(self) -> None:
        client = FakeLLMClient(["不是 JSON"])
        renderer = SummaryRenderer(client, model="m")
        _, channel = await renderer.render(_report())
        assert channel == "template"
