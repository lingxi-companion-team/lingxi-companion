"""诊断数据组装 —— 教师端「诊断」折叠区的纯函数数据源。

放服务端而不是 client 算的理由与 :mod:`app.present.summary` 相同：
**client 是覆盖率 omit 区**，任何「从 payload 里推出一个数」的规则都不该长在那里——
长在那里就没护栏。诊断行只是**呈现已有数据**（在线数 / 各状态数 / stale 数），
不产生新的可见性判定：payload 里有什么就算什么，学生端不调它，调了也看不到
超过其 payload 的东西（A2 的防线在 hub 组 payload 那层，不在这一层）。

本模块唯一的外部依赖是 ``json``（算 payload 字节数）与 :mod:`app.present.summary`
的 ``LABEL_TEXT``（标签中文名保持同一出处，不另起一套）。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from app.present.summary import LABEL_TEXT

__all__ = [
    "diag_lines",
]

#: 各状态行的输出顺序：与 :data:`app.present.summary.LABEL_TEXT` 一致，
#: 专注/困惑/分神/未知 —— 与汇总面板 4 行同序，教师读起来不用换脑子。
_STATE_ORDER = ("focused", "confused", "distracted", "unknown")


def _count_states(payload: Mapping[str, Any]) -> dict[str, int]:
    """数 grid 里各状态标签出现次数。

    口径：只数**有 label 且 label 在已知集合内**的格；hidden / 无结果 / 未知标签
    一律不进任何一档（「在线」的定义与 :func:`app.present.summary.summarize` 一致：
    has_state 才算）。未知标签不抛错——这是诊断，不是校验器。
    """
    counts = dict.fromkeys(_STATE_ORDER, 0)
    grid = payload.get("grid")
    if not isinstance(grid, list):
        return counts
    for cell in grid:
        if not isinstance(cell, Mapping):
            continue
        state = cell.get("state")
        if not isinstance(state, Mapping):
            continue
        label = state.get("label")
        if isinstance(label, str) and label in counts:
            counts[label] += 1
    return counts


def _count_stale(payload: Mapping[str, Any]) -> int:
    """数 grid 里 stale=True 的格数（「维持中」不是新结果，值得单独盯）。"""
    grid = payload.get("grid")
    if not isinstance(grid, list):
        return 0
    n = 0
    for cell in grid:
        if not isinstance(cell, Mapping):
            continue
        state = cell.get("state")
        if isinstance(state, Mapping) and state.get("stale"):
            n += 1
    return n


def _payload_bytes(payload: Mapping[str, Any]) -> int:
    """payload 序列化后的 UTF-8 字节数。

    用 ``ensure_ascii=False`` 与 :meth:`app.hub._respond` 同口径——诊断的意义是
    「线上真实发了多少字节」，不是「转义后多少字节」。
    """
    return len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def diag_lines(
    payload: Mapping[str, Any],
    *,
    viewer: str,
    now_ts: float,
    refresh_ms: float | None = None,
) -> list[str]:
    """组装诊断行（纯函数）。

    参数：
    - ``payload``：本观看者收到的 snapshot payload（按观看者裁剪后的那份）。
    - ``viewer``：观看者 id（显示用，不回查 hub——本模块不知道 hub 存在）。
    - ``now_ts``：当前时间戳（保留给将来的「距上次成功刷新 N 秒」行；现在先
      收进签名，避免以后加行时改签名）。
    - ``refresh_ms``：本次刷新耗时（毫秒），由 client 实测后传入；``None``
      表示「没测」，该行显示 ``--`` 而不是编一个数。

    返回固定结构的行列表（顺序恒定，client 逐行 render 即可）：

    1. ``viewer=<id>``
    2. ``在线 N``（has_state 口径）
    3-6. 各状态一行（顺序：专注/困惑/分神/未知）
    7. ``stale N``（维持中格数）
    8. ``payload N B``（序列化字节数）
    9. ``刷新 N ms`` 或 ``刷新 -- ms``
    """
    _ = now_ts  # 见 docstring：签名预留，本版不使用。
    counts = _count_states(payload)
    online = sum(counts.values())
    lines = [
        f"viewer={viewer}",
        f"在线 {online}",
    ]
    lines.extend(f"{LABEL_TEXT[key]} {counts[key]}" for key in _STATE_ORDER)
    lines.append(f"stale {_count_stale(payload)}")
    lines.append(f"payload {_payload_bytes(payload)} B")
    ms = "--" if refresh_ms is None else f"{refresh_ms:.0f}"
    lines.append(f"刷新 {ms} ms")
    return lines
