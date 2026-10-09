from __future__ import annotations

import json

import pytest

from app.services.llm.base import (
    CostRecord,
    CostReport,
    LLMFailure,
    LLMFailureKind,
    extract_json_object,
)
from app.services.llm.fake import FakeLLMClient


class TestExtractJson:
    def test_plain_json(self) -> None:
        assert extract_json_object('{"a": 1}') == {"a": 1}

    def test_code_fenced(self) -> None:
        content = '```json\n{"a": 1}\n```'
        assert extract_json_object(content) == {"a": 1}

    def test_wrapped_in_text(self) -> None:
        content = '好的，以下是结果：{"a": 1} 希望有帮助'
        assert extract_json_object(content) == {"a": 1}

    def test_no_json_raises_schema(self) -> None:
        with pytest.raises(LLMFailure) as exc_info:
            extract_json_object("完全没有 JSON")
        assert exc_info.value.kind == LLMFailureKind.SCHEMA

    def test_non_object_root_raises(self) -> None:
        with pytest.raises(LLMFailure):
            extract_json_object("[1, 2, 3]")


class TestFailureClassification:
    def test_retryable_kinds(self) -> None:
        for kind in (LLMFailureKind.RATE_LIMIT, LLMFailureKind.SERVER, LLMFailureKind.NETWORK):
            assert LLMFailure(kind, "x").retryable is True

    def test_non_retryable_kinds(self) -> None:
        for kind in (LLMFailureKind.AUTH, LLMFailureKind.SCHEMA, LLMFailureKind.EMPTY):
            assert LLMFailure(kind, "x").retryable is False


class TestFakeLLMClient:
    async def test_scripted_dict_output(self) -> None:
        client = FakeLLMClient([{"action": "STOP_WITH_GAPS"}])
        result = await client.chat_json(system="s", user="u", purpose="test")
        assert json.loads(result.content)["action"] == "STOP_WITH_GAPS"
        assert client.calls[0]["purpose"] == "test"

    async def test_scripted_exception(self) -> None:
        failure = LLMFailure(LLMFailureKind.AUTH, "bad key")
        client = FakeLLMClient([failure])
        with pytest.raises(LLMFailure):
            await client.chat_json(system="s", user="u", purpose="test")

    async def test_exhausted_script(self) -> None:
        client = FakeLLMClient()
        with pytest.raises(LLMFailure):
            await client.chat_json(system="s", user="u", purpose="test")

    async def test_queue_appends(self) -> None:
        client = FakeLLMClient()
        client.queue({"a": 1})
        result = await client.chat_json(system="s", user="u", purpose="test")
        assert json.loads(result.content) == {"a": 1}


class TestCostReport:
    def test_aggregation(self) -> None:
        report = CostReport()
        report.add(
            CostRecord(
                purpose="compile", model="m", input_tokens=100, output_tokens=50, latency_ms=10
            )
        )
        report.add(
            CostRecord(
                purpose="summary", model="m", input_tokens=200, output_tokens=25, latency_ms=20
            )
        )
        assert report.call_count == 2
        assert report.total_tokens == 375
        payload = report.as_dict()
        assert payload["total_tokens"] == 375
        assert [call["purpose"] for call in payload["calls"]] == ["compile", "summary"]

    def test_empty_report(self) -> None:
        report = CostReport()
        assert report.total_tokens == 0
        assert report.as_dict()["calls"] == []
