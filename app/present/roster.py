"""成员明细表的行数据（纯逻辑，无 GUI 依赖）。

v7 三栏仪表盘中栏下半部是一张「成员明细表」：头像圆 + 编号 + 状态胶囊 + 置信度条 +
更新时间。本模块只产出**行数据**，表格怎么画（自绘 Canvas、hover 高亮、分页）是
``app/client/table.py`` 的事。

筛选为什么必须在 present 层（决策 ④）
--------------------------------------
右面板的「筛选」要求**真生效** —— 按状态过滤表格（与宫格）。这是**判定**，
不是画法：过滤后剩哪些行、按什么顺序，属于可测试的纯逻辑。若写在 client 里，
``app/client/`` 被覆盖率 omit，这条规则就永远没有护栏。所以
:func:`roster_rows` 接收 ``label_filter`` 与 ``query``，client 只把参数透传。

行从哪来
--------
用 :func:`app.present.grid.grid_cells` 挑「占格的人」—— 复用它那条
「有状态 或 已隐藏 或 已关闭感知」的判据，**不重写第二份**。教师不占格，
所以名单里自然没有教师（他正是看名单的人）。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from app.envelope import ParticipantState
from app.present.colors import CLOSED_COLOR, color_for_key
from app.present.grid import grid_cells
from app.present.status import freshness
from app.present.summary import CLOSED_KEY, CLOSED_TEXT, LABEL_ORDER, LABEL_TEXT

__all__ = ["MASKED_TEXT", "RosterRow", "roster_rows"]

#: 状态被掩去时宫格里的显示文案（需求文档 §模块二.2 的「仍占格」状态）。
#:
#: 刻意不用警示色也不用「已屏蔽」这类带惩罚意味的词：公开开关是**权利**，
#: 不是过失。文案与样式都要让「选择不公开」看起来是一个正常选项。
MASKED_TEXT = "已隐藏"

#: 名单里的状态档位顺序（供 ``group_by_label`` 排序用）：四个情感档在前，已关闭在最后。
_GROUP_ORDER: dict[str, int] = {key: index for index, key in enumerate(LABEL_ORDER)}
_GROUP_ORDER[CLOSED_KEY] = len(_GROUP_ORDER)


@dataclass(frozen=True)
class RosterRow:
    """名单表里的一行。

    Attributes:
        participant_id: 参与者编号（头像圆里显示它的首字）。
        initial: 头像圆里的字符（编号首字，大写）。
        label_key: 线路格式的状态键；**已关闭者为** :data:`app.present.summary.CLOSED_KEY`。
        label_text: 中文展示名（状态胶囊里的字）。
        color: 状态胶囊的色点色；已关闭者用 :data:`app.present.colors.CLOSED_COLOR`。
        confidence: 置信度（0~1）；已关闭 / 无状态者为 ``None``（画成空条）。
        freshness: ``"fresh"`` / ``"aging"`` / ``"stale"`` —— 供「更新时间」列的视觉分层。
        closed: 是否已关闭感知。
        hidden: 是否对同学隐藏（教师端仍看得到状态，此标志只影响标签旁的小注）。
        ts: 该行状态的时间戳（已关闭者为参与者自身 ``ts``）。
        masked: 该行的状态**被按观看者掩去了**（本人选择不公开，且观看者不是本人）。
            与 ``hidden`` 的区别：``hidden`` 是「这个人开着未公开开关」，
            ``masked`` 是「**在你眼里**他的状态是空白的」—— 本人看自己时
            ``hidden`` 为真而 ``masked`` 为假。宫格要的是后者（见 :attr:`cell_text`）。
    """

    participant_id: str
    initial: str
    label_key: str
    label_text: str
    color: str
    confidence: float | None
    freshness: str
    closed: bool
    hidden: bool
    ts: float
    masked: bool = False

    @property
    def cell_text(self) -> str:
        """宫格格子里显示的那两个字。

        与 :attr:`label_text` 的差别只在「被掩去」这一种情形：

        - **名单**里显示「未知」—— 那张表是按状态分档的，掩去者确实没有可读的判定，
          归到灰色档（``unknown``）才与筛选、计数自洽；
        - **宫格**里显示「已隐藏」—— 宫格是给人扫视的，「这个人选择不公开」
          比「系统没测出来」是更有用的信息，而且这正是需求文档 §模块二.2
          要求「仍占格」的那个状态。

        本人看自己时 ``masked`` 为假，所以这里显示的是真实状态名 ——
        这正是 §模块二.2「默认仅本人可见」的字面意思。
        """
        if self.closed:
            return CLOSED_TEXT
        if self.masked:
            return MASKED_TEXT
        return self.label_text


def _to_row(participant: ParticipantState, now_ts: float) -> RosterRow:
    """把单个参与者摊成一行。已关闭者走一条独立分支（它没有 ``state``）。"""
    initial = participant.participant_id[:1].upper() or "?"
    if participant.closed:
        return RosterRow(
            participant_id=participant.participant_id,
            initial=initial,
            label_key=CLOSED_KEY,
            label_text=CLOSED_TEXT,
            color=CLOSED_COLOR,
            confidence=None,
            freshness="stale",
            closed=True,
            hidden=participant.hidden,
            ts=participant.ts,
        )
    state = participant.state
    if state is None:
        # 走到这里只可能是「被隐藏且本帧无状态」—— 占格但没有任何可展示读数。
        # 归到 UNKNOWN 档：与「本帧未形成判定」同一视觉处理，不给它单开一档。
        # （宫格上两者会分开显示 —— 见 ``RosterRow.cell_text``。）
        unknown_key = LABEL_ORDER[-1]
        return RosterRow(
            participant_id=participant.participant_id,
            initial=initial,
            label_key=unknown_key,
            label_text=LABEL_TEXT[unknown_key],
            color=color_for_key(unknown_key),
            confidence=None,
            freshness="stale",
            closed=False,
            hidden=participant.hidden,
            ts=participant.ts,
            masked=participant.hidden,
        )
    return RosterRow(
        participant_id=participant.participant_id,
        initial=initial,
        label_key=state.label.value,
        label_text=LABEL_TEXT[state.label.value],
        color=color_for_key(state.label.value),
        confidence=state.confidence,
        freshness=freshness(now_ts, state),
        closed=False,
        hidden=participant.hidden,
        ts=state.timestamp,
    )


def roster_rows(
    participants: Iterable[ParticipantState],
    now_ts: float,
    *,
    label_filter: str | None = None,
    query: str | None = None,
    group_by_label: bool = False,
) -> list[RosterRow]:
    """产出名单表的行（可筛选）。

    Args:
        participants: 全部参与者（含教师，会被自然排除）。
        now_ts: 当前时刻，用于算新鲜度。
        label_filter: 只保留该状态键的行。``None`` / ``""`` / ``"all"`` 表示不筛；
            也可以是 :data:`app.present.summary.CLOSED_KEY` 只看已关闭者；
            **非法键不抛错**，按「不筛」处理（同 ``color_for_key`` 的兜底约定 ——
            展示层不为一条异常输入整体崩掉）。
        query: 按 ``participant_id`` 做**大小写不敏感的子串**匹配；``None`` / 空串不筛。
        group_by_label: ``True`` 时按状态档位分组排序（专注 → … → 已关闭），
            ``False``（默认）按编号字典序。默认取编号序是为了**稳定** —— 成员换状态
            时行不会整表跳动；分组序更适合「按状态扫读」的场景，由调用方按需开。

    Returns:
        行列表。顺序确定可复现。
    """
    cells = grid_cells(participants)
    rows = [_to_row(participant, now_ts) for participant in cells]

    if label_filter and label_filter != "all":
        if label_filter in _GROUP_ORDER:
            rows = [row for row in rows if row.label_key == label_filter]
        # 非法键：静默不筛（见 docstring）。

    if query:
        needle = query.strip().casefold()
        if needle:
            rows = [row for row in rows if needle in row.participant_id.casefold()]

    if group_by_label:
        rows.sort(
            key=lambda row: (
                _GROUP_ORDER.get(row.label_key, len(_GROUP_ORDER)),
                row.participant_id,
            )
        )
    else:
        rows.sort(key=lambda row: row.participant_id)
    return rows
