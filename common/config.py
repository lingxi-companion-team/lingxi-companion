"""配置加载。

设计目标：**零第三方依赖**。项目目前只声明了 numpy + pytest，不应为了读一个
阈值文件就引入 PyYAML。这里实现一个仅覆盖 ``configs/thresholds.yaml`` 所需
子集的最小解析器（两层映射 + 标量 + 注释 + 行内注释）。

若将来配置结构变复杂（嵌套列表、多行字符串等），再切换到 PyYAML 并在
``requirements-dev.txt`` 中声明即可，调用方接口不变。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

__all__ = [
    "DEFAULT_CONFIG_PATH",
    "DEFAULT_E0",
    "DEFAULT_K",
    "get",
    "load_config",
]

#: 仓库根目录（本文件位于 common/ 下）
_REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = _REPO_ROOT / "configs" / "thresholds.yaml"

# ---------------------------------------------------------------------------
# weights.e0 / weights.k 的兜底值（配置文件缺失或该项缺失时使用）
# ---------------------------------------------------------------------------
# 为什么放在这里、而不是 ``fusion/weights.py``：
#   这两个值**同时**被融合层（权重平衡点）与 ``common/agent_base.py``
#   （环境智能体降级路径的中性 ``env_score``）使用，而 ``common`` 不能反向
#   依赖 ``fusion``。放在 config 层，两边都只从这一处取。
# 历史教训：这两个值曾在三处各写一遍（weights.py、fusion_engine.py、
#   agent_base.py），其中一处漏改成 0.5 就造成了「环境未知时偷偷偏向行为通道」
#   的真实缺陷。数值只保留**一处定义**。

#: logistic 中心点：E 等于该值时两路权重相等，也是「环境未知」时的中性取值
DEFAULT_E0 = 0.6
#: logistic 斜率：越大过渡越陡，8.0 约对应 0.35~0.85 的过渡带
DEFAULT_K = 8.0

_CACHE: dict[str, Any] = {}


def _parse_scalar(text: str) -> Any:
    """把 YAML 标量文本转成 Python 值。"""
    text = text.strip()
    if not text:
        return ""
    lowered = text.lower()
    if lowered in ("true", "yes", "on"):
        return True
    if lowered in ("false", "no", "off"):
        return False
    if lowered in ("null", "none", "~"):
        return None
    # 尝试数值
    try:
        if "." in text or "e" in lowered:
            return float(text)
        return int(text)
    except ValueError:
        pass
    # 去引号
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        return text[1:-1]
    return text


def _strip_comment(line: str) -> str:
    """去掉行内注释，但保护引号内的 #。"""
    out: list[str] = []
    quote: str | None = None
    for ch in line:
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
            out.append(ch)
            continue
        if ch == "#":
            break
        out.append(ch)
    return "".join(out).rstrip()


def load_config(path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """读取配置文件。

    结果按绝对路径缓存，避免每帧重复解析。配置在开发期几乎不变，
    若需热重载请调用 ``load_config.cache_clear()``。

    Returns:
        两层嵌套 dict，如 ``{"weights": {"e0": 0.6, "k": 8.0}, ...}``。
        文件不存在时返回空 dict（调用方使用各自默认值）。
    """
    target = Path(path) if path else DEFAULT_CONFIG_PATH
    key = str(target.resolve())

    if key in _CACHE:
        return _CACHE[key]

    if not target.is_file():
        _CACHE[key] = {}
        return {}

    config: dict[str, Any] = {}
    current: str | None = None

    with target.open(encoding="utf-8") as handle:
        for raw in handle:
            line = _strip_comment(raw)
            if not line.strip():
                continue

            indent = len(line) - len(line.lstrip())
            stripped = line.strip()
            if ":" not in stripped:
                continue

            key_part, _, value_part = stripped.partition(":")
            key_part = key_part.strip()
            value_part = value_part.strip()

            if indent == 0:
                if value_part:
                    # 顶层标量
                    config[key_part] = _parse_scalar(value_part)
                    current = None
                else:
                    # 顶层映射开始
                    config[key_part] = {}
                    current = key_part
            else:
                if current is None:
                    continue
                if value_part:
                    config[current][key_part] = _parse_scalar(value_part)
                else:
                    config[current][key_part] = {}

    _CACHE[key] = config
    return config


def get(*keys: str, default: Any = None, config: dict[str, Any] | None = None) -> Any:
    """按路径取配置值。

    用法::

        get("weights", "e0")              # -> 0.6
        get("smoothing", "window")        # -> 3
        get("nope", default=42)           # -> 42
    """
    node: Any = config if config is not None else load_config()
    for key in keys:
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return node
