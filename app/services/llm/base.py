"""LLM 网关基础类型：失败分类、JSON 容错提取、调用结果。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import StrEnum


class LLMFailureKind(StrEnum):
    AUTH = "auth"  # 401/403，不重试
    RATE_LIMIT = "rate_limited"  # 429，可重试
    SERVER = "server_error"  # 5xx，可重试
    NETWORK = "network"  # 连接/超时，可重试
    SCHEMA = "invalid_schema"  # 输出不合法，不重试（交给上层降级梯）
    EMPTY = "empty_content"  # 空输出，不重试


@dataclass(slots=True)
class LLMFailure(Exception):
    """带分类的 LLM 调用失败；上层按 kind 决定重试或降级。"""

    kind: LLMFailureKind
    message: str
    status_code: int | None = None

    @property
    def retryable(self) -> bool:
        return self.kind in {
            LLMFailureKind.RATE_LIMIT,
            LLMFailureKind.SERVER,
            LLMFailureKind.NETWORK,
        }


@dataclass(slots=True)
class LLMResult:
    """一次成功的模型调用；token 计量进入 CostReport。"""

    content: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    purpose: str = ""
    latency_ms: int = 0

    def parsed_json(self) -> dict:
        return extract_json_object(self.content)


_JSON_OBJECT_PATTERN = re.compile(r"\{.*\}", re.DOTALL)


def extract_json_object(content: str) -> dict:
    """容错提取：模型可能用代码块或前后缀文本包裹 JSON。"""
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text, flags=re.DOTALL)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = _JSON_OBJECT_PATTERN.search(text)
        if match is None:
            raise LLMFailure(LLMFailureKind.SCHEMA, "response contains no JSON object") from None
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise LLMFailure(LLMFailureKind.SCHEMA, f"JSON decode failed: {exc}") from exc
    if not isinstance(parsed, dict):
        raise LLMFailure(LLMFailureKind.SCHEMA, "JSON root is not an object")
    return parsed


@dataclass(slots=True)
class CostRecord:
    """单次调用成本记录；按 Run 聚合。"""

    purpose: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    outcome: str = "ok"  # ok / upgraded / failed

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(slots=True)
class CostReport:
    """Run 级成本账本：模型 token 与调用次数（Provider 调用在报告 budget 中另行统计）。"""

    records: list[CostRecord] = field(default_factory=list)

    def add(self, record: CostRecord) -> None:
        self.records.append(record)

    @property
    def total_tokens(self) -> int:
        return sum(record.total_tokens for record in self.records)

    @property
    def call_count(self) -> int:
        return len(self.records)

    def as_dict(self) -> dict:
        return {
            "total_tokens": self.total_tokens,
            "call_count": self.call_count,
            "calls": [
                {
                    "purpose": record.purpose,
                    "model": record.model,
                    "input_tokens": record.input_tokens,
                    "output_tokens": record.output_tokens,
                    "latency_ms": record.latency_ms,
                    "outcome": record.outcome,
                }
                for record in self.records
            ],
        }


class RunCostRecorder:
    """按 Run 包装共享 LLM 客户端：成功与失败都记入该 Run 的 CostReport。

    共享客户端（网关/Fake）自带进程级账本；Run 级成本必须独立计量。
    """

    def __init__(self, inner, cost: CostReport) -> None:  # noqa: ANN001 - 结构化类型避免循环导入
        self.inner = inner
        self.cost = cost

    async def chat_json(
        self,
        *,
        system: str,
        user: str,
        model: str | None = None,
        purpose: str,
        temperature: float = 0.0,
        max_tokens: int = 2048,
    ):
        import time

        started = time.monotonic()
        try:
            result = await self.inner.chat_json(
                system=system,
                user=user,
                model=model,
                purpose=purpose,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except LLMFailure as failure:
            self.cost.add(
                CostRecord(
                    purpose=purpose,
                    model=model or "",
                    input_tokens=0,
                    output_tokens=0,
                    latency_ms=int((time.monotonic() - started) * 1000),
                    outcome=f"failed:{failure.kind.value}",
                )
            )
            raise
        self.cost.add(
            CostRecord(
                purpose=purpose,
                model=result.model,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                latency_ms=result.latency_ms,
            )
        )
        return result
