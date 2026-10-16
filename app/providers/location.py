"""地点归一化：检索层共享工具，兼容“北京延庆/延庆县/密云区周边”等自然写法。"""

from __future__ import annotations


def normalize_location(location: str) -> str:
    """去掉分隔符与行政后缀，保留可比较的核心地名。

    仅做检索侧的宽松归一化（不改变意图原文）；验证层不依赖本函数。
    后缀只剥一层，避免“张家口市宣化区”这类复合行政写法被过度截断。
    """
    normalized = location.replace("·", "").replace(" ", "").casefold()
    for suffix in ("周边", "市", "区", "县", "省"):
        if normalized.endswith(suffix) and len(normalized) > len(suffix):
            normalized = normalized[: -len(suffix)]
            break
    return normalized
