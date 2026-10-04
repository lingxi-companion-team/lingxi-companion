"""``app.present.filters`` 的守卫。

这一层最容易出的错是**「标签上的数」与「点进去看到的人」对不上** ——
用户第一眼就会觉得这个页面不可信，而那种错不会让任何别的测试变红。
所以这里的核心是一条不变式：计数与筛选读同一份列表、用同一个 :func:`bucket_of`。

第二类要挡的是**与 ``roster_rows`` 的分档漂移**：两处各写一套「谁算哪一档」，
今天一致、改一处就不一致了。这里用交叉断言把两者焊在一起。
"""

from __future__ import annotations

import pytest

from app.envelope import ParticipantState
from app.present.filters import (
    ATTENTION_ORDER,
    BUCKET_ALL,
    BUCKET_UNKNOWN,
    BUCKETS,
    SORT_ATTENTION,
    SORT_ID,
    SORTS,
    bucket_of,
    filter_counts,
    filter_options,
    normalize_filter,
    normalize_sort,
    sort_options,
    sort_rows,
)
from app.present.grid import grid_cells
from app.present.roster import roster_rows
from app.present.summary import CLOSED_KEY, LABEL_ORDER
from common.perception_types import EmotionLabel, FinalState

NOW = 100.0


def _state(label: EmotionLabel, *, stale: bool = False) -> FinalState:
    return FinalState(label=label, timestamp=NOW, stale=stale, confidence=0.8)


def _student(
    pid: str,
    label: EmotionLabel | None = EmotionLabel.FOCUSED,
    *,
    hidden: bool = False,
    closed: bool = False,
) -> ParticipantState:
    return ParticipantState(
        pid,
        state=None if label is None else _state(label),
        hidden=hidden,
        closed=closed,
    )


def _mixed() -> list[ParticipantState]:
    """一批覆盖全部分档的人：四个情感档 + 未判定 + 已隐藏 + 已关闭。"""
    return [
        _student("s01", EmotionLabel.FOCUSED),
        _student("s02", EmotionLabel.CONFUSED),
        _student("s03", EmotionLabel.CONFUSED),
        _student("s04", EmotionLabel.DISTRACTED),
        _student("s05", EmotionLabel.UNKNOWN),
        _student("s06", None),  # 本帧还没有结果
        _student("s07", None, hidden=True),  # 被别人掩去的未公开者
        _student("s08", None, closed=True),
    ]


# ── 档位集合 ────────────────────────────────────────────────────────────


def test_buckets_are_all_plus_the_four_labels_plus_closed() -> None:
    assert BUCKETS == (BUCKET_ALL, *LABEL_ORDER, CLOSED_KEY)


def test_unknown_bucket_is_the_grey_legend_entry() -> None:
    """需求文档的图例是「灰色 = 不确定/未公开」—— 一档覆盖两种情形。"""
    assert BUCKET_UNKNOWN == LABEL_ORDER[-1] == "unknown"


def test_attention_order_covers_exactly_the_buckets_minus_all() -> None:
    """关注度排序必须覆盖每一档且不重复 —— 否则会有行掉到「未登记」的兜底档里。"""
    assert sorted(ATTENTION_ORDER) == sorted(k for k in BUCKETS if k != BUCKET_ALL)


def test_attention_order_puts_confused_and_distracted_first() -> None:
    """§模块二.3 要的是「快速筛选查看困惑/分心的参与者」，所以这两档必须在最前。"""
    assert ATTENTION_ORDER[:2] == ("confused", "distracted")


def test_attention_order_differs_from_the_legend_order() -> None:
    """两者刻意不同：图例顺序适合逐档核对，关注度顺序适合「谁该先被看见」。"""
    assert ATTENTION_ORDER != (*LABEL_ORDER, CLOSED_KEY)


# ── bucket_of ───────────────────────────────────────────────────────────


def test_bucket_of_every_participant_is_a_known_bucket() -> None:
    for participant in _mixed():
        assert bucket_of(participant) in BUCKETS


def test_closed_beats_everything_else() -> None:
    """同时「关闭感知」与「未公开」时按已关闭算 —— 关闭是更强的事实。"""
    assert bucket_of(_student("s01", None, hidden=True, closed=True)) == CLOSED_KEY


def test_bucket_of_agrees_with_roster_rows() -> None:
    """交叉断言：两处分档必须逐人一致，否则「筛选」与「名单」会各说各话。

    只对**占格的人**比 —— 不占格的人（刚进房间、没结果、也没隐藏）宫格里根本没有
    他那一格，``roster_rows`` 自然也不给他出列，两边都「没有」，谈不上一致不一致。
    """
    for participant in grid_cells(_mixed()):
        row = roster_rows([participant], NOW)[0]
        assert bucket_of(participant) == row.label_key, participant.participant_id


def test_masked_and_unjudged_land_in_the_same_bucket() -> None:
    """「被别人掩去」与「本帧没测出来」在展示上是同一档（灰色），照文档图例。"""
    masked = _student("s07", None, hidden=True)
    unjudged = _student("s06", None)
    assert bucket_of(masked) == bucket_of(unjudged) == BUCKET_UNKNOWN


# ── 计数 ────────────────────────────────────────────────────────────────


def test_counts_always_carry_every_bucket() -> None:
    counts = filter_counts([])
    assert set(counts) == set(BUCKETS)
    assert all(value == 0 for value in counts.values())


def test_counts_are_exhaustive_and_exclusive() -> None:
    """核心不变式：各档之和恒等于总数。"""
    counts = filter_counts(_mixed())
    parts = sum(counts[key] for key in BUCKETS if key != BUCKET_ALL)
    assert parts == counts[BUCKET_ALL]


def test_non_occupying_participants_are_not_counted() -> None:
    """**不占格的人不进计数** —— 否则「全部 8」旁边只会看到 7 格。

    ``_mixed()`` 里有 8 个人，其中 ``s06``（没结果、没隐藏、没关闭）不占格，
    所以计入的只有 7 个。这条把「计数 = 占格的人」这个口径钉死。
    """
    everyone = _mixed()
    assert len(everyone) == 8
    assert len(grid_cells(everyone)) == 7
    assert filter_counts(everyone)[BUCKET_ALL] == 7


def test_counts_match_the_filtered_rows_one_for_one() -> None:
    """**这条是整套筛选的立足点**：标签上写几，点进去就该有几个。

    若不变量破了（比如计数改从 ``summary`` 取，而 summary 只统计已公开者），
    这条会红 —— 而那种不一致在界面上看起来只是「数字有点怪」，很难被发现。
    """
    everyone = _mixed()
    counts = filter_counts(everyone)
    for option in filter_options():
        rows = roster_rows(everyone, NOW, label_filter=option.key)
        assert len(rows) == counts[option.key], f"{option.key} 档：标签 {counts[option.key]}"
        # 反向：筛出来的每一行的档位都必须是这一档。
        for row in rows:
            assert row.label_key == option.key or option.key == BUCKET_ALL


def test_counts_put_the_masked_person_in_the_grey_bucket() -> None:
    counts = filter_counts(_mixed())
    # s05（UNKNOWN）与 s07（被掩去）都算灰色档。
    assert counts[BUCKET_UNKNOWN] == 2


# ── 档位声明 ────────────────────────────────────────────────────────────


def test_filter_options_cover_every_bucket_in_order() -> None:
    assert tuple(option.key for option in filter_options()) == BUCKETS


def test_filter_option_texts_are_non_empty_and_unique() -> None:
    texts = [option.text for option in filter_options()]
    assert all(texts)
    assert len(texts) == len(set(texts))


def test_every_filter_option_explains_itself() -> None:
    for option in filter_options():
        assert len(option.hint) >= 6, f"{option.key}: hint 太短"


def test_the_grey_filter_says_it_covers_two_things() -> None:
    """「未判定」那一档必须自己说清楚它同时包含「没测出来」与「不公开」。"""
    option = next(o for o in filter_options() if o.key == BUCKET_UNKNOWN)
    assert "不公开" in option.hint
    assert "未形成判定" in option.hint


def test_sort_options_cover_every_sort_in_order() -> None:
    assert tuple(option.key for option in sort_options()) == SORTS


def test_sort_option_texts_are_non_empty_and_unique() -> None:
    texts = [option.text for option in sort_options()]
    assert all(texts)
    assert len(texts) == len(set(texts))


# ── 兜底 ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("bad", [None, "", "  ", "FOCUSED", "hidden", "42", "all!"])
def test_illegal_filter_falls_back_to_all(bad: str | None) -> None:
    assert normalize_filter(bad) == BUCKET_ALL


@pytest.mark.parametrize("good", list(BUCKETS))
def test_legal_filter_passes_through(good: str) -> None:
    assert normalize_filter(good) == good


@pytest.mark.parametrize("bad", [None, "", "label", "state", "attention!"])
def test_illegal_sort_falls_back_to_id(bad: str | None) -> None:
    assert normalize_sort(bad) == SORT_ID


@pytest.mark.parametrize("good", list(SORTS))
def test_legal_sort_passes_through(good: str) -> None:
    assert normalize_sort(good) == good


# ── 排序 ────────────────────────────────────────────────────────────────


def test_sort_by_id_is_stable_and_alphabetical() -> None:
    rows = roster_rows(_mixed(), NOW)
    shuffled = list(reversed(rows))
    assert [r.participant_id for r in sort_rows(shuffled, SORT_ID)] == [
        "s01",
        "s02",
        "s03",
        "s04",
        "s05",
        "s07",
        "s08",
    ]


def test_sort_by_attention_puts_confused_and_distracted_first() -> None:
    rows = roster_rows(_mixed(), NOW)
    ordered = [r.label_key for r in sort_rows(rows, SORT_ATTENTION)]
    assert ordered[:3] == ["confused", "confused", "distracted"]


def test_sort_by_attention_follows_the_declared_order_exactly() -> None:
    rows = roster_rows(_mixed(), NOW)
    ordered = sort_rows(rows, SORT_ATTENTION)
    rank = {label: index for index, label in enumerate(ATTENTION_ORDER)}
    ranks = [rank[r.label_key] for r in ordered]
    assert ranks == sorted(ranks), "关注度排序没有真的按 ATTENTION_ORDER 排"


def test_sort_by_attention_is_deterministic() -> None:
    """同档内按编号 —— 同一批数据、同一个档位，结果永远一样。"""
    rows = roster_rows(_mixed(), NOW)
    first = [r.participant_id for r in sort_rows(rows, SORT_ATTENTION)]
    second = [r.participant_id for r in sort_rows(list(reversed(rows)), SORT_ATTENTION)]
    assert first == second


def test_illegal_sort_key_does_not_crash() -> None:
    rows = roster_rows(_mixed(), NOW)
    assert [r.participant_id for r in sort_rows(rows, "nonsense")] == [
        r.participant_id for r in sort_rows(rows, SORT_ID)
    ]


def test_sorting_never_drops_or_adds_rows() -> None:
    rows = roster_rows(_mixed(), NOW)
    for key in SORTS:
        assert sorted(r.participant_id for r in sort_rows(rows, key)) == sorted(
            r.participant_id for r in rows
        )
