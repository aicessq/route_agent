"""OpenAI 兼容 LLM 网关：仅负责传输、鉴权、失败分类与 token 计量。

模型选择、降级梯、语义校验都在上层（intent_compiler / repair / summary），
网关不理解业务。
"""

from __future__ import annotations

import time
from typing import Protocol

import httpx

from app.services.llm.base import CostRecord, CostReport, LLMFailure, LLMFailureKind, LLMResult


class LLMClient(Protocol):
    """业务侧依赖的最小接口；Fake 与真实网关同构。"""

    async def chat_json(
        self,
        *,
        system: str,
        user: str,
        model: str | None = None,
        purpose: str,
        temperature: float = 0.0,
        max_tokens: int = 2048,
    ) -> LLMResult: ...


class OpenAICompatGateway:
    """OpenAI 兼容 /chat/completions 客户端；response_format=json_object。"""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        default_model: str,
        timeout_seconds: float = 30.0,
        cost_report: CostReport | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.default_model = default_model
        self.timeout = httpx.Timeout(timeout_seconds)
        self.cost_report = cost_report or CostReport()
        self._transport = transport

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
        resolved_model = model or self.default_model
        payload = {
            "model": resolved_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        started = time.monotonic()
        last_failure: LLMFailure | None = None
        # 可重试失败（429/5xx/网络）只重试一次；不可重试直接上抛。
        for attempt in range(2):
            try:
                async with httpx.AsyncClient(
                    timeout=self.timeout, transport=self._transport
                ) as client:
                    response = await client.post(
                        f"{self.base_url}/chat/completions",
                        json=payload,
                        headers={"Authorization": f"Bearer {self.api_key}"},
                    )
                    response.raise_for_status()
                result = self._parse_response(response.json(), resolved_model, purpose, started)
                self.cost_report.add(
                    CostRecord(
                        purpose=purpose,
                        model=resolved_model,
                        input_tokens=result.input_tokens,
                        output_tokens=result.output_tokens,
                        latency_ms=result.latency_ms,
                        outcome="ok" if attempt == 0 else "ok_retry",
                    )
                )
                return result
            except Exception as exc:  # noqa: BLE001 - 统一分类后按 kind 处理
                failure = exc if isinstance(exc, LLMFailure) else _status_to_failure(exc)
                last_failure = failure
                if not failure.retryable:
                    break
        assert last_failure is not None
        self.cost_report.add(
            CostRecord(
                purpose=purpose,
                model=resolved_model,
                input_tokens=0,
                output_tokens=0,
                latency_ms=int((time.monotonic() - started) * 1000),
                outcome=f"failed:{last_failure.kind.value}",
            )
        )
        raise last_failure

    @staticmethod
    def _parse_response(
        payload: dict,
        model: str,
        purpose: str,
        started: float,
    ) -> LLMResult:
        choices = payload.get("choices") or []
        if not choices:
            raise LLMFailure(LLMFailureKind.EMPTY, "no choices in response")
        content = choices[0].get("message", {}).get("content")
        if not content or not str(content).strip():
            raise LLMFailure(LLMFailureKind.EMPTY, "empty content")
        usage = payload.get("usage") or {}
        return LLMResult(
            content=str(content),
            model=model,
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
            purpose=purpose,
            latency_ms=int((time.monotonic() - started) * 1000),
        )


def _status_to_failure(exc: Exception) -> LLMFailure:
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status in (401, 403):
            return LLMFailure(LLMFailureKind.AUTH, f"http {status}", status)
        if status == 429:
            return LLMFailure(LLMFailureKind.RATE_LIMIT, "http 429", status)
        if status >= 500:
            return LLMFailure(LLMFailureKind.SERVER, f"http {status}", status)
        return LLMFailure(LLMFailureKind.SCHEMA, f"http {status}", status)
    if isinstance(exc, httpx.TimeoutException):
        return LLMFailure(LLMFailureKind.NETWORK, f"timeout: {exc}")
    if isinstance(exc, httpx.HTTPError):
        return LLMFailure(LLMFailureKind.NETWORK, f"network: {exc}")
    return LLMFailure(LLMFailureKind.EMPTY, f"unexpected: {exc}")
