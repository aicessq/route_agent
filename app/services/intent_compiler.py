"""Intent Compiler：自然语言 → RouteIntent。

降级梯（与设计冻结一致）：
1. 主模型（小模型）+ 本地 JSON/Schema 校验；
2. Schema 失败或语义校验失败时升级大模型一次；
3. 仍失败 → 规则提取（正则，仅明确数值条件）→ 部分意图交由 Completeness Gate 追问；
4. 全程失败不猜测：宁可部分意图 + 追问，也不生成无依据约束。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from pydantic import ValidationError

from app.domain.intent import RouteIntent
from app.services.llm.base import LLMFailure, LLMResult, extract_json_object
from app.services.llm.gateway import LLMClient
from app.services.llm.prompts import (
    INTENT_COMPILE_SYSTEM,
    INTENT_COMPILE_USER_TEMPLATE,
)

PROMPT_VERSION = "intent-compile.v1"

_RISK_KEYWORDS: list[tuple[str, str]] = [
    ("悬崖", "risk_cliff_exposure"),
    ("临崖", "risk_cliff_exposure"),
    ("过河", "risk_river_crossing"),
    ("涉水", "risk_river_crossing"),
    ("封路", "risk_road_closure"),
    ("塌方", "risk_landslide"),
    ("落石", "risk_landslide"),
    ("结冰", "risk_ice"),
]

_FACILITY_KEYWORDS: list[tuple[str, str]] = [
    ("水源", "water_source"),
    ("补水", "water_source"),
    ("商店", "store"),
    ("补给", "store"),
    ("厕所", "restroom"),
    ("营地", "camp"),
]

_DISTANCE_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*(?:公里|千米|km)", re.IGNORECASE)
_DISTANCE_LIMIT_PATTERN = re.compile(
    r"(?:不超过|以内|最多|小于|低于|≤)\s*(\d+(?:\.\d+)?)\s*(?:公里|千米|km)|"
    r"(\d+(?:\.\d+)?)\s*(?:公里|千米|km)\s*以内",
    re.IGNORECASE,
)
_GAIN_PATTERN = re.compile(
    r"(?:爬升|累计爬升|上升|海拔上升|累计上升|升降)(?:.*?)(?:不超过|最多|小于|低于|≤)?\s*(\d+(?:\.\d+)?)\s*米",
    re.IGNORECASE,
)
_DURATION_PATTERN = re.compile(
    r"(?:不超过|最多|以内|约|大约|左右)?\s*(\d+(?:\.\d+)?)\s*(?:小时|个半小时|小时半)|"
    r"(\d+(?:\.\d+)?)\s*小时",
    re.IGNORECASE,
)
_AGE_PATTERN = re.compile(r"(\d+)\s*岁")
_LOCATION_STOPWORDS = ("带", "去", "想", "要", "在", "从")


class SemanticValidationError(ValueError):
    """JSON 合法但业务语义越权（如模型自造放宽授权）。"""


_RELAX_CONSENT_MARKERS = ("放宽", "可以到", "接受", "没有合适的", "适当")
_RELAXABLE_FIELDS = {
    "max_distance_km",
    "max_elevation_gain_m",
    "max_duration_minutes",
    "min_rating",
}


def _semantic_check(payload: dict, query: str) -> None:
    """服务端语义校验：放宽授权必须有用户原话依据，且只能落在可放宽字段上。"""
    relaxation = payload.get("relaxation")
    if isinstance(relaxation, dict) and relaxation:
        if not any(marker in query for marker in _RELAX_CONSENT_MARKERS):
            raise SemanticValidationError("relaxation without user consent language in query")
        illegal = set(relaxation) - _RELAXABLE_FIELDS
        if illegal:
            raise SemanticValidationError(f"non-relaxable fields: {sorted(illegal)}")


@dataclass(slots=True)
class CompileOutcome:
    """编译结果：成功意图、降级原因、是否走了规则通道。"""

    intent: RouteIntent | None
    source: str  # llm_primary / llm_fallback / rule_extraction / failed
    detail: str = ""


class IntentCompiler:
    def __init__(
        self,
        client: LLMClient,
        *,
        primary_model: str,
        fallback_model: str | None = None,
    ) -> None:
        self.client = client
        self.primary_model = primary_model
        self.fallback_model = fallback_model

    async def compile(self, query: str) -> CompileOutcome:
        user_prompt = INTENT_COMPILE_USER_TEMPLATE.format(query=query)
        # 第一梯：主模型。
        primary = await self._call_or_none(
            system=INTENT_COMPILE_SYSTEM,
            user=user_prompt,
            model=self.primary_model,
            purpose="intent_compile_primary",
        )
        if primary is not None:
            outcome, reason = self._validate(primary, source="llm_primary", query=query)
            if outcome is not None:
                return outcome
            failure_detail = reason
        else:
            failure_detail = "llm_call_failed"
        # 第二梯：升级大模型一次（仅当配置了 fallback 且主模型失败/输出不合法）。
        if self.fallback_model and self.fallback_model != self.primary_model:
            fallback = await self._call_or_none(
                system=INTENT_COMPILE_SYSTEM,
                user=user_prompt,
                model=self.fallback_model,
                purpose="intent_compile_fallback",
            )
            if fallback is not None:
                outcome, reason = self._validate(fallback, source="llm_fallback", query=query)
                if outcome is not None:
                    return outcome
                failure_detail = reason
        # 第三梯：规则提取，产出部分意图（location 可能缺失 → Completeness Gate 追问）。
        partial = extract_by_rules(query)
        return CompileOutcome(
            intent=partial,
            source="rule_extraction",
            detail=failure_detail or "llm_unavailable_or_invalid",
        )

    async def _call_or_none(
        self, *, system: str, user: str, model: str, purpose: str
    ) -> LLMResult | None:
        try:
            return await self.client.chat_json(
                system=system, user=user, model=model, purpose=purpose
            )
        except LLMFailure:
            # 网关已做传输级重试；这里失败交给降级梯，不再重复调用同模型。
            return None

    def _validate(
        self, result: LLMResult, *, source: str, query: str
    ) -> tuple[CompileOutcome | None, str]:
        """返回 (编译结果, 失败原因)；失败原因进入降级 detail 供观测。"""
        try:
            payload = extract_json_object(result.content)
            _semantic_check(payload, query)
            # 模型可能包一层壳（如 {"intent": {...}}）。
            if "intent" in payload and isinstance(payload["intent"], dict):
                payload = payload["intent"]
            payload.pop("schema_version", None)
            # OpenAI 兼容输出会用 null 表示“字段缺省”，剥掉后交给领域默认值。
            payload = {key: value for key, value in payload.items() if value is not None}
            # 模型偶发夹带无害额外字段：编译层丢弃而不是整单拒绝
            # （领域模型对 API 输入仍 extra=forbid；丢弃的键不可能进入意图）。
            known_fields = set(RouteIntent.model_fields)
            payload = {key: value for key, value in payload.items() if key in known_fields}
            intent = RouteIntent.model_validate(payload)
        except (LLMFailure, ValidationError, SemanticValidationError, ValueError) as exc:
            detail = f"{type(exc).__name__}: {exc}"
            return None, detail[:200]
        return CompileOutcome(intent=intent, source=source), ""


def extract_by_rules(query: str) -> RouteIntent:
    """确定性规则提取：只提取明确数值/关键词，宁缺勿猜。"""
    payload: dict = {}

    distance = _search_number(query, _DISTANCE_LIMIT_PATTERN) or _search_number(
        query, _DISTANCE_PATTERN
    )
    if distance is not None:
        payload["max_distance_km"] = distance

    gain = _search_number(query, _GAIN_PATTERN)
    if gain is not None:
        payload["max_elevation_gain_m"] = gain

    hours = _search_number(query, _DURATION_PATTERN)
    if hours is not None:
        payload["max_duration_minutes"] = round(hours * 60)

    risks = [code for keyword, code in _RISK_KEYWORDS if keyword in query]
    if risks:
        payload["avoid_risk_types"] = risks

    facilities = [code for keyword, code in _FACILITY_KEYWORDS if keyword in query]
    if facilities:
        payload["required_facilities"] = facilities

    ages = [int(age) for age in _AGE_PATTERN.findall(query)]
    if ages:
        payload["participants"] = {"children": True, "child_ages": ages}

    payload["location"] = _guess_location(query)
    if payload["location"] is None:
        payload.pop("location")
    return RouteIntent.model_validate(payload)


def _search_number(query: str, pattern: re.Pattern) -> float | None:
    match = pattern.search(query)
    if match is None:
        return None
    for group in match.groups():
        if group is not None:
            return float(group)
    return None


def _guess_location(query: str) -> str | None:
    """极简地名启发：仅用于规则通道的部分意图，缺失时由门禁追问。"""
    for city in ("北京", "上海", "广州", "深圳", "杭州", "成都", "西安", "武汉", "南京", "重庆"):
        if city in query:
            return city
    return None


def today_iso() -> str:
    return date.today().isoformat()
