"""真实 LLM 服务冒烟测试：验证 Agnes AI 接入（鉴权/JSON 输出/token 计量）。

用法：
    python scripts/llm_smoke.py          # 走 secrets/llm.env 配置
    python scripts/llm_smoke.py --intent # 顺带跑一次真实意图编译

不进入 pytest（网络依赖）；CI 一律用 FakeLLMClient。
密钥只在内存中出现，不写入任何输出文件。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.intent_compiler import IntentCompiler  # noqa: E402
from app.services.llm.base import CostReport, LLMFailure, extract_json_object  # noqa: E402
from app.services.llm.gateway import OpenAICompatGateway  # noqa: E402
from app.settings import get_settings  # noqa: E402

QUERY = "周六带8岁孩子去北京延庆徒步，8公里以内，爬升不超过300米，2小时左右，有水源补给，避开悬崖"


async def main(run_intent: bool) -> int:
    settings = get_settings()
    if not settings.llm_api_key or not settings.llm_base_url:
        print("SKIP: secrets/llm.env 未配置（LLM_BASE_URL/LLM_API_KEY）")
        return 0

    cost = CostReport()
    gateway = OpenAICompatGateway(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        default_model=settings.llm_model,
        timeout_seconds=settings.llm_timeout_seconds,
        cost_report=cost,
    )

    # 1) 基础连通 + JSON 输出
    print(f"[1] chat_json 基础调用 model={settings.llm_model} ...")
    try:
        result = await gateway.chat_json(
            system="你是 JSON 输出器。只输出 JSON。",
            user='请输出 {"ok": true, "n": 42}',
            purpose="smoke",
        )
        payload = extract_json_object(result.content)
        assert payload.get("ok") is True, f"unexpected payload: {payload}"
        tokens = f"{result.input_tokens}+{result.output_tokens}"
        print(f"    OK latency={result.latency_ms}ms tokens={tokens}")
    except LLMFailure as exc:
        print(f"    FAIL kind={exc.kind} message={exc.message}")
        return 1

    # 2) 真实意图编译
    if run_intent:
        print("[2] IntentCompiler 真实编译 ...")
        compiler = IntentCompiler(gateway, primary_model=settings.llm_model)
        outcome = await compiler.compile(QUERY)
        print(f"    source={outcome.source} detail={outcome.detail or '-'}")
        if outcome.intent is None:
            print("    FAIL: no intent produced")
            return 1
        intent = outcome.intent
        print(f"    location={intent.location}")
        print(f"    max_distance_km={intent.max_distance_km}")
        print(f"    max_elevation_gain_m={intent.max_elevation_gain_m}")
        print(f"    avoid_risk_types={[r.value for r in intent.avoid_risk_types]}")
        print(f"    max_duration_minutes={intent.max_duration_minutes}")
        print(f"    required_facilities={[f.value for f in intent.required_facilities]}")
        print(f"    participants={intent.participants.model_dump()}")

    print(f"[cost] calls={cost.call_count} total_tokens={cost.total_tokens}")
    print("SMOKE PASSED")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--intent", action="store_true", help="顺带跑真实意图编译")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.intent)))
