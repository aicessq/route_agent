"""确定性合成路线生成器：为本地 ES 检索通道与规模测评提供模拟数据。

硬规则（见 AGENTS.md 与 data/README.md）：
- 合成数据必须带 ``source_kind=synthetic`` 与 ``fixture-synthetic-*`` 指纹，
  不得冒充真实轨迹；产物不入 git，生成器与参数（seed/count）入仓可复现。
- 全部字段由 SHA-512(seed:index) 派生，不使用随机库：同参数两次生成逐字段一致。
- 风险/设施是显式标注（None 表示证据缺失，验证层应判 UNKNOWN），不从几何数据推断。
- 索引字段（距离/爬升/评分）按固定缺失率置 None：ES range 过滤会静默丢弃缺字段文档，
  这是刻意的——用于刻画并测出"索引缺数据候选被无声过滤"的结构性行为。
"""

from __future__ import annotations

import hashlib
import struct

GENERATOR_VERSION = "synthetic-1"

# 10 城 × 10 区 = 100 个地点；均为户外徒步常见区域，仅作模拟地名池使用。
_CITY_DISTRICTS: dict[str, list[str]] = {
    "北京": ["延庆", "怀柔", "门头沟", "密云", "房山", "昌平", "平谷", "海淀", "顺义", "大兴"],
    "张家口": ["崇礼", "赤城", "张北", "蔚县", "怀来", "涿鹿", "宣化", "沽源", "阳原", "康保"],
    "承德": ["滦平", "兴隆", "围场", "丰宁", "平泉", "宽城", "隆化", "承德县", "鹰手营子", "双桥"],
    "天津": ["蓟州", "武清", "宝坻", "静海", "宁河", "滨海", "西青", "津南", "北辰", "东丽"],
    "保定": ["涞水", "涞源", "易县", "阜平", "唐县", "顺平", "满城", "曲阳", "徐水", "竞秀"],
    "石家庄": ["井陉", "赞皇", "平山", "灵寿", "行唐", "鹿泉", "元氏", "赵县", "正定", "藁城"],
    "大同": ["浑源", "灵丘", "阳高", "天镇", "广灵", "左云", "云州", "新荣", "平城", "云冈"],
    "忻州": ["五台", "宁武", "代县", "繁峙", "静乐", "神池", "五寨", "岢岚", "河曲", "保德"],
    "晋中": ["左权", "和顺", "榆社", "介休", "灵石", "寿阳", "昔阳", "平遥", "太谷", "祁县"],
    "洛阳": ["栾川", "嵩县", "汝阳", "宜阳", "洛宁", "新安", "孟津", "偃师", "伊川", "龙门"],
}

LOCATIONS: list[str] = [
    f"{city}·{district}" for city, districts in _CITY_DISTRICTS.items() for district in districts
]

_THEMES = ["环线", "山脊", "溪谷", "林道", "古道", "长城", "湖畔", "河谷", "草甸", "森林"]
_SUFFIXES = ["线", "环线", "穿越线", "休闲线", "徒步线"]
_TAGS = ["亲子", "观景", "山脊", "溪谷", "环线", "露营", "古道", "新手友好", "四季", "轻装"]
_RISK_TYPES = [
    "risk_cliff_exposure",
    "risk_river_crossing",
    "risk_road_closure",
    "risk_landslide",
    "risk_ice",
]
_FACILITIES = ["water_source", "store", "restroom", "camp"]

# 索引字段缺失率（百分数）：测"缺数据候选被 range 过滤静默丢弃"的行为。
_DISTANCE_MISSING_PCT = 8
_ELEVATION_MISSING_PCT = 8
_RATING_MISSING_PCT = 6
# 证据字段缺失率（None → UNKNOWN）：风险 25%、设施 20%。
_RISK_MISSING_PCT = 25
_FACILITY_MISSING_PCT = 20


def _derive(seed: str, index: int) -> tuple[tuple[int, ...], str]:
    """把 seed:index 散列成 16 个 32 位字 + 十六进制指纹，供字段分槽取值与打标。"""
    digest = hashlib.sha512(f"{seed}:{index}".encode()).digest()
    return struct.unpack(">16I", digest), digest.hex()[:8]


def _pick(words: tuple[int, ...], count_slot: int, cursor_slot: int, pool: list[str]) -> list[str]:
    """按散列字从池中取不重复子集；count_slot 位为 0 时返回空集（证据在但为空）。"""
    count = words[count_slot] % 4
    chosen: list[str] = []
    cursor = words[cursor_slot]
    for _ in range(count):
        # LCG 步进避免重复元素；跳过已选项后继续探测。
        cursor = (cursor * 48271 + 11) % (2**31 - 1)
        item = pool[cursor % len(pool)]
        if item not in chosen:
            chosen.append(item)
    return chosen


def _missing_flags(words: tuple[int, ...]) -> tuple[bool, bool, bool, bool, bool]:
    """从单个散列字按 6-bit 位切片出五个缺失标志，各片独立映射到目标缺失率。"""
    flag_word = words[7]

    def flag(shift: int, pct: int) -> bool:
        return (flag_word >> shift & 0x3F) < pct * 64 // 100

    return (
        flag(0, _DISTANCE_MISSING_PCT),
        flag(6, _ELEVATION_MISSING_PCT),
        flag(12, _RATING_MISSING_PCT),
        flag(18, _RISK_MISSING_PCT),
        flag(24, _FACILITY_MISSING_PCT),
    )


def generate_routes(count: int, *, seed: str = "synthetic-1") -> list[dict[str, object]]:
    """生成 count 条确定性合成路线文档，字段名与 ES Provider 读取口径一致。"""
    if count < 0:
        raise ValueError("count must be non-negative")
    routes: list[dict[str, object]] = []
    title_seq: dict[str, int] = {}
    for index in range(1, count + 1):
        w, digest = _derive(seed, index)
        route_id = f"syn-{index:06d}"
        location = LOCATIONS[w[0] % len(LOCATIONS)]
        district = location.split("·", 1)[1]
        theme = _THEMES[w[1] % len(_THEMES)]
        suffix = _SUFFIXES[w[2] % len(_SUFFIXES)]
        seq = title_seq.get(location, 0) + 1
        title_seq[location] = seq

        dist_missing, elev_missing, rating_missing, risk_missing, fac_missing = _missing_flags(w)
        distance = None if dist_missing else round(2 + w[3] % 240 / 10, 1)
        elevation = None if elev_missing else 40 + w[5] % 1200
        rating = None if rating_missing else round((30 + w[4] % 21) / 10, 1)

        # 时长与距离/爬升正相关，再叠加散列抖动：分布形态接近真实徒步耗时。
        duration = 60 + (int(distance * 12) if distance else 0) + (elevation or 0) // 20 + w[6] % 30

        risks = None if risk_missing else _pick(w, 8, 9, _RISK_TYPES)
        facilities = None if fac_missing else _pick(w, 10, 11, _FACILITIES)

        routes.append(
            {
                "route_id": route_id,
                "title": f"{district}{theme}{suffix}·{seq:03d}",
                "location": location,
                "source_kind": "synthetic_fixture",
                "entity_revision": f"{route_id}-r1",
                "source_fingerprint": f"fixture-synthetic-{digest}",
                "distance_km": distance,
                "elevation_gain_m": elevation,
                "duration_minutes": duration,
                "rating": rating,
                "tags": _pick(w, 12, 13, _TAGS),
                "risk_types": risks,
                "facilities": facilities,
                "generator_version": GENERATOR_VERSION,
            }
        )
    return routes


def summarize(routes: list[dict[str, object]]) -> dict[str, object]:
    """灌库脚本与测评报告用的分布统计：缺失计数 + 城市级数量。"""
    total = len(routes)
    if total == 0:
        return {"total": 0}

    def missing(field: str) -> int:
        return sum(1 for r in routes if r.get(field) is None)

    by_city: dict[str, int] = {}
    for route in routes:
        city = str(route["location"]).split("·", 1)[0]
        by_city[city] = by_city.get(city, 0) + 1
    return {
        "total": total,
        "locations": len({r["location"] for r in routes}),
        "missing_distance": missing("distance_km"),
        "missing_elevation": missing("elevation_gain_m"),
        "missing_rating": missing("rating"),
        "missing_risk_evidence": missing("risk_types"),
        "missing_facility_evidence": missing("facilities"),
        "by_city": dict(sorted(by_city.items())),
    }
