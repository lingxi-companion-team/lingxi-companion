"""``app.present.kpi`` 的测试：5 张卡片的顺序、占比与置灰。"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from app.envelope import ParticipantState
from app.present.colors import CLOSED_COLOR, DIM_COLOR
from app.present.kpi import KpiCard, kpi_cards
from app.present.summary import CLOSED_KEY, CLOSED_TEXT, LABEL_ORDER, summarize
from common.perception_types import EmotionLabel, FinalState


def _student(pid: str, label: EmotionLabel) -> ParticipantState:
    return ParticipantState(pid, state=FinalState(label=label, timestamp=1.0, confidence=0.7))


def _closed(pid: str) -> ParticipantState:
    return ParticipantState(pid, closed=True)


def test_cards_are_five_in_fixed_order() -> None:
    """4 个情感分量 + 1 个已关闭，顺序恒定（0 人也保留占位）。"""
    cards = kpi_cards(summarize([]))
    assert [card.key for card in cards] == [*LABEL_ORDER, CLOSED_KEY]
    assert len(cards) == 5


def test_empty_class_greys_every_card() -> None:
    """全班 0 人时五张卡全部置灰 —— 没有「有颜色的 0」。"""
    cards = kpi_cards(summarize([]))
    assert all(card.dim for card in cards)
    assert {card.color for card in cards} == {DIM_COLOR}
    assert all(card.count == 0 and card.ratio == 0.0 for card in cards)


def test_counts_and_ratios() -> None:
    people = [
        _student("s01", EmotionLabel.FOCUSED),
        _student("s02", EmotionLabel.FOCUSED),
        _student("s03", EmotionLabel.CONFUSED),
        _closed("s04"),
    ]
    cards = {card.key: card for card in kpi_cards(summarize(people))}
    assert cards["focused"].count == 2
    assert cards["focused"].ratio == pytest.approx(0.5)
    assert cards["confused"].count == 1
    assert cards["distracted"].count == 0
    assert cards["distracted"].dim is True
    assert cards[CLOSED_KEY].count == 1
    assert cards[CLOSED_KEY].ratio == pytest.approx(0.25)
    assert cards[CLOSED_KEY].label == CLOSED_TEXT


def test_closed_card_uses_closed_color_when_nonzero() -> None:
    """已关闭卡在有人时用专属色，不是置灰色，也不是某个情感色。"""
    cards = {card.key: card for card in kpi_cards(summarize([_closed("s01")]))}
    assert cards[CLOSED_KEY].color == CLOSED_COLOR
    assert cards[CLOSED_KEY].dim is False


def test_ratios_sum_to_one_including_closed() -> None:
    """在线非 0 时：四张情感卡占比 + 已关闭占比 == 1。"""
    people = [
        _student("s01", EmotionLabel.FOCUSED),
        _student("s02", EmotionLabel.CONFUSED),
        _closed("s03"),
    ]
    cards = kpi_cards(summarize(people))
    assert sum(card.ratio for card in cards) == pytest.approx(1.0)


def test_nonzero_card_keeps_state_color_and_is_not_dim() -> None:
    focused_only = summarize([_student("s01", EmotionLabel.FOCUSED)])
    cards = {card.key: card for card in kpi_cards(focused_only)}
    card = cards["focused"]
    assert card.dim is False
    assert card.color != DIM_COLOR
    assert card.label == "专注"


def test_card_is_frozen() -> None:
    card = kpi_cards(summarize([]))[0]
    assert isinstance(card, KpiCard)
    with pytest.raises(FrozenInstanceError):
        card.count = 5  # type: ignore[misc]
