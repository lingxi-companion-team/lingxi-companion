"""参与者区的**筛选、排序与计数**（纯逻辑，无 GUI 依赖）。

需求文档 §模块二.3 的原话是「支持按状态排序，可快速筛选查看困惑/分心的参与者」。
这一条把三件事绑在了一起，所以本模块同时管三件 —— 拆开会让「筛出来的行」与
「标签上写的数」各有各的口径：

1. **分档**（:func:`bucket_of`）—— 一个人属于哪一档；
2. **计数**（:func:`filter_counts`）—— 每档各有多少人；
3. **排序**（:func:`sort_rows`）—— 按编号还是按「需要关注的程度」。

「未判定」与「未公开」为什么合成一档
------------------------------------
需求文档 §模块二.3 自己就是这么分的：它给的图例是
「绿色=专注、黄色=困惑、红色=分心、**灰色=不确定/未公开**」—— 灰色同时覆盖
「系统本帧没测出来」与「本人选择不公开」。所以 :data:`BUCKET_UNKNOWN` 不是偷懒，
是**照文档办事**。这也让 :func:`bucket_of` 与 :func:`app.present.roster.roster_rows`
的分档完全一致（``test_filters.py`` 有交叉断言防止两者漂移）。

计数为什么从**列表**算而不是从 ``summary`` 取
---------------------------------------------
``payload["summary"]`` 的口径是「**已公开状态**的人」，它按设计就**不含**未公开者。
若标签上的数字取自 ``summary``、而列表来自全部占格的人，就会出现
「标签写 3、点进去 5 个人」——用户第一眼就会觉得这个页面不可信。

所以计数与筛选**必须同源**：都从同一份占格列表算。这不是取舍，是唯一自洽的做法。
（``summary`` 仍然是 KPI 卡片与聚合概览的数据源，那里的分母本就是「已公开」，
口径不同、用途不同，两边都对。）

排序为什么不是「专注在最前」
----------------------------
:func:`app.present.roster.roster_rows` 的 ``group_by_label`` 按
:data:`app.present.summary.LABEL_ORDER` 排（专注 → 困惑 → 分心 → 未知），
那是**图例顺序**，适合「逐档核对」。

但 §模块二.3 要的是「快速筛选查看**困惑/分心**的参与者」—— 对发起人来说，
一屏里最该前跳的是需要关注的人。所以本模块的 :data:`SORT_ATTENTION` 用
:data:`ATTENTION_ORDER`（困惑 → 分心 → 未判定 → 专注 → 已关闭）：
**同一批数据，换一个「谁先被看见」的意图**。
两个顺序都保留、都正确，因为它们的用途不同；界面给发起人一个开关去选。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from app.envelope import ParticipantState
from app.present.grid import grid_cells
from app.present.roster import RosterRow
from app.present.summary import CLOSED_KEY, CLOSED_TEXT, LABEL_ORDER, LABEL_TEXT

__all__ = [
    "ATTENTION_ORDER",
    "BUCKET_ALL",
    "BUCKET_UNKNOWN",
    "BUCKETS",
    "SORT_ATTENTION",
    "SORT_ID",
    "SORTS",
    "FilterOption",
    "SortOption",
    "bucket_of",
    "filter_counts",
    "filter_options",
    "normalize_filter",
    "normalize_sort",
    "sort_options",
    "sort_rows",
]

#: 「不筛」档。
BUCKET_ALL = "all"

#: 「未判定 / 未公开」档 —— 即需求文档图例里的**灰色**。
#: 它是 :data:`app.present.summary.LABEL_ORDER` 的最后一档（``"unknown"``），
#: 这里给一个语义化的别名，免得各处硬编码字符串。
BUCKET_UNKNOWN = LABEL_ORDER[-1]

#: 全部可选档位（顺序 = 界面上的展示顺序）：不筛 → 四个情感档 → 已关闭感知。
BUCKETS: tuple[str, ...] = (BUCKET_ALL, *LABEL_ORDER, CLOSED_KEY)

#: 「按状态」排序时，**需要关注的在最前**。
#:
#: 与 :data:`app.present.summary.LABEL_ORDER`（图例顺序）刻意不同 —— 详见模块 docstring。
ATTENTION_ORDER: tuple[str, ...] = (
    "confused",
    "distracted",
    BUCKET_UNKNOWN,
    "focused",
    CLOSED_KEY,
)

#: 按编号排（稳定，默认）。成员换状态时行不会整表跳动。
SORT_ID = "id"
#: 按「需要关注的程度」排（:data:`ATTENTION_ORDER`）。
SORT_ATTENTION = "attention"

SORTS: tuple[str, ...] = (SORT_ID, SORT_ATTENTION)


@dataclass(frozen=True)
class FilterOption:
    """筛选档位。

    Attributes:
        key: 档位键，取自 :data:`BUCKETS`。``all`` 表示不筛。
        text: 中文展示名。
        hint: 一句话说明这一档包含谁 —— 「未判定」那一档必须解释清楚
            它同时包含「系统没测出来」与「本人不公开」。
    """

    key: str
    text: str
    hint: str


@dataclass(frozen=True)
class SortOption:
    """排序档位。"""

    key: str
    text: str
    hint: str


#: 筛选档位的展示声明。文案与 ``LABEL_TEXT`` 同源（四个情感档直接取它），
#: 只有 ``all`` 与「未判定」两档另写 —— 它们不是情感标签，不进 ``LABEL_TEXT``。
_FILTER_HINTS: dict[str, str] = {
    BUCKET_ALL: "房间里所有占格的人。",
    BUCKET_UNKNOWN: "灰色档：本帧未形成判定，或本人选择不公开。",
    CLOSED_KEY: "主动关掉感知采集的人。",
}
_FILTER_TEXTS: dict[str, str] = {
    BUCKET_ALL: "全部",
    BUCKET_UNKNOWN: "未判定/未公开",
    CLOSED_KEY: CLOSED_TEXT,
}


def filter_options() -> list[FilterOption]:
    """全部筛选档位（顺序 = :data:`BUCKETS`）。"""
    options: list[FilterOption] = []
    for key in BUCKETS:
        text = _FILTER_TEXTS.get(key, LABEL_TEXT.get(key, key))
        hint = _FILTER_HINTS.get(key, f"本帧判定为「{text}」的人。")
        options.append(FilterOption(key=key, text=text, hint=hint))
    return options


def sort_options() -> list[SortOption]:
    """全部排序档位（顺序 = :data:`SORTS`）。"""
    return [
        SortOption(
            key=SORT_ID,
            text="按编号",
            hint="稳定的编号序 —— 有人换状态时行不会整表跳动。",
        ),
        SortOption(
            key=SORT_ATTENTION,
            text="按关注度",
            hint="困惑 → 分心 → 未判定 → 专注 → 已关闭，需要关注的在最前。",
        ),
    ]


def normalize_filter(key: str | None) -> str:
    """把任意输入收敛到合法档位；非法值按 :data:`BUCKET_ALL`（不筛）处理。

    与 :func:`app.present.roster.roster_rows` 的兜底约定一致：展示层不为一条
    异常输入整体崩掉。也正因为要兜底，这个函数必须留在**有测试的层**。
    """
    return key if key in BUCKETS else BUCKET_ALL


def normalize_sort(key: str | None) -> str:
    """把任意输入收敛到合法排序档；非法值按 :data:`SORT_ID` 处理。"""
    return key if key in SORTS else SORT_ID


def bucket_of(participant: ParticipantState) -> str:
    """一个参与者落在哪一档（返回 :data:`BUCKETS` 里的键）。

    判据顺序与 :func:`app.present.roster.roster_rows` 完全一致：
    已关闭 → 无状态（未判定 / 被掩去）→ 按标签。
    ``test_filters.py`` 有交叉断言：对同一批参与者，
    ``bucket_of(p)`` 必须恒等于 ``roster_rows([p], ...)[0].label_key``。
    """
    if participant.closed:
        return CLOSED_KEY
    state = participant.state
    if state is None:
        return BUCKET_UNKNOWN
    return state.label.value


def filter_counts(participants: Iterable[ParticipantState]) -> dict[str, int]:
    """按档位计数。

    **先过一遍 :func:`app.present.grid.grid_cells`** —— 计数的是「占格的人」，
    与宫格、名单读的是同一份集合。这一步不能省：刚进房间、还没出结果、也没隐藏的人
    **不占格**（``occupies_cell`` 为假），宫格里根本没有他那一格；若计数把他算进去，
    「全部 8」旁边只会看到 7 格。这类不一致在界面上看起来只是「数字有点怪」，
    极难被发现，所以在函数内部就掐掉，不指望每个调用方都记得先过滤。

    返回的 dict **恒含** :data:`BUCKETS` 的全部键（0 人的档位也在，界面不必判空），
    且满足::

        counts["all"] == sum(counts[k] for k in BUCKETS if k != "all")

    这条不变式正是「标签上的数」与「点进去看到的人数」永远对得上的保证 ——
    因为它俩读的是同一个函数（``test_filters.py`` 钉住）。
    """
    counts = dict.fromkeys(BUCKETS, 0)
    total = 0
    for participant in grid_cells(participants):
        total += 1
        counts[bucket_of(participant)] += 1
    counts[BUCKET_ALL] = total
    return counts


def sort_rows(rows: Sequence[RosterRow], key: str) -> list[RosterRow]:
    """按档位重排已经筛好的行。

    非法排序键按 :data:`SORT_ID` 处理（同 :func:`normalize_sort` 的兜底）。

    两种排序都以 ``participant_id`` 作第二键，所以结果是**确定可复现**的 ——
    同一批数据、同一个档位，永远得到同一个顺序，不会因为输入顺序不同而抖动。
    """
    resolved = normalize_sort(key)
    if resolved == SORT_ATTENTION:
        rank = {label: index for index, label in enumerate(ATTENTION_ORDER)}
        return sorted(
            rows,
            key=lambda row: (rank.get(row.label_key, len(rank)), row.participant_id),
        )
    return sorted(rows, key=lambda row: row.participant_id)
