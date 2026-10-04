"""KPI 卡片数据（纯逻辑，无 GUI 依赖）。

v7 三栏仪表盘顶部有一排 KPI 卡片，每张显示「一个分量的人数 + 占比」。
这层只产出**数据**，卡片怎么画（色条、大数字、占比条）是 ``app/client/kpi.py`` 的事。

为什么这条规则必须在 present 层
--------------------------------
「KPI 卡片有哪几张、什么顺序、0 人时什么色」都是**展示规则**，不是画法。
``app/client/`` 整体 omit 于覆盖率，规则写在那儿就等于没有护栏 —— 所以这里产出
一个 ``list[KpiCard]``，client 只负责遍历着画。

五张卡（设计稿 §「KPI 卡片组」）
--------------------------------
顺序固定为 ``专注 / 困惑 / 分神 / 未知 / 已关闭感知``：

- 前四张取自 :data:`app.present.summary.LABEL_ORDER`（**0 人也保留占位**，
  不随人数增减 —— 与设计稿 §10.1 d1 的「分量恒在」一致）；
- 第 5 张是「已关闭感知」。它**不是一种情感**（是用户主动关掉采集），
  故取 :data:`app.present.summary.CLOSED_KEY` 与
  :data:`app.present.colors.CLOSED_COLOR`，而不是挤进 ``STATE_COLORS``。

0 人的分量按既有规则**置灰**（``color_for_key_count`` 的同一条口径），
这样「当前无人」与「该状态有很多人」在一眼扫过时不会混。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.present.colors import CLOSED_COLOR, DIM_COLOR, color_for_key_count
from app.present.summary import CLOSED_KEY, CLOSED_TEXT, LABEL_ORDER, LABEL_TEXT, Summary

__all__ = ["KpiCard", "kpi_cards"]


@dataclass(frozen=True)
class KpiCard:
    """一张 KPI 卡片的全部数据。

    Attributes:
        key: 线路格式的分量键（``"focused"`` … 或 ``"closed"``）。
        label: 中文展示名（``"专注"`` … / ``"已关闭感知"``）。
        count: 该分量的人数。
        ratio: 该分量占**在线人数**的比例（0~1）；在线为 0 时是 ``0.0``。
        color: 该分量的展示色；0 人时置灰（:data:`app.present.colors.DIM_COLOR`）。
        dim: 是否处于「0 人置灰」态。client 据此决定数字要不要一起淡下去。
    """

    key: str
    label: str
    count: int
    ratio: float
    color: str
    dim: bool


def kpi_cards(summary: Summary) -> list[KpiCard]:
    """把 :class:`~app.present.summary.Summary` 摊成 5 张 KPI 卡片。

    返回顺序**恒为** ``LABEL_ORDER + (closed,)``，与人数无关 —— 卡片位置固定，
    教师扫视时不必每次重新找「分神在哪张」。

    ``closed`` 卡的比例分母同样是 ``online_count``（含已关闭者），所以
    「四张情感卡占比之和 + 已关闭占比 == 1」在在线人数非 0 时恒成立。
    """
    cards: list[KpiCard] = []
    for key in LABEL_ORDER:
        count = int(summary.by_label.get(key, 0))
        cards.append(
            KpiCard(
                key=key,
                label=LABEL_TEXT[key],
                count=count,
                ratio=float(summary.ratio.get(key, 0.0)),
                color=color_for_key_count(key, count),
                dim=count <= 0,
            )
        )
    closed_count = summary.closed_count
    cards.append(
        KpiCard(
            key=CLOSED_KEY,
            label=CLOSED_TEXT,
            count=closed_count,
            ratio=summary.closed_ratio,
            color=DIM_COLOR if closed_count <= 0 else CLOSED_COLOR,
            dim=closed_count <= 0,
        )
    )
    return cards
