"""确定性 Fake LLM 客户端：脚本化响应序列，支持失败注入。"""

from __future__ import annotations

from collections.abc import Sequence

from app.services.llm.base import CostRecord, CostReport, LLMFailure, LLMFailureKind, LLMResult

SCRIPTED_OUTPUT = str | dict | Exception


class FakeLLMClient:
    """按调用顺序弹出脚本化结果：dict 视为 JSON 输出，Exception 直接抛出。

    用途：意图编译/修复规划/摘要的确定性测试与基线对照。
    """

    def __init__(
        self,
        outputs: Sequence[SCRIPTED_OUTPUT] = (),
        *,
        model: str = "fake-small",
        cost_report: CostReport | None = None,
    ) -> None:
        self.outputs: list[SCRIPTED_OUTPUT] = list(outputs)
        self.model = model
        self.cost_report = cost_report or CostReport()
        self.calls: list[dict] = []

    def queue(self, *outputs: SCRIPTED_OUTPUT) -> None:
        self.outputs.extend(outputs)

    async def chat_json(
        self,
        *,
        system: str,
        user: str,
        model: str | None = None,
        purpose: str,
        temperature: float = 0.0,
        max_tokens: int = 2048,
    ) -> LLMResult:
        self.calls.append({"system": system, "user": user, "model": model, "purpose": purpose})
        if not self.outputs:
            raise LLMFailure(LLMFailureKind.EMPTY, "fake llm exhausted scripted outputs")
        item = self.outputs.pop(0)
        if isinstance(item, Exception):
            self.cost_report.add(
                CostRecord(
                    purpose=purpose,
                    model=model or self.model,
                    input_tokens=0,
                    output_tokens=0,
                    latency_ms=0,
                    outcome=f"failed:{getattr(item, 'kind', 'error')}",
                )
            )
            raise item
        content = _dumps_compact(item) if isinstance(item, dict) else str(item)
        result = LLMResult(
            content=content,
            model=model or self.model,
            input_tokens=max(1, len(system) // 4),
            output_tokens=max(1, len(content) // 4),
            purpose=purpose,
            latency_ms=1,
        )
        self.cost_report.add(
            CostRecord(
                purpose=purpose,
                model=result.model,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                latency_ms=result.latency_ms,
            )
        )
        return result


def _dumps_compact(payload: dict) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
