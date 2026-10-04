"""``app.present.trend`` 的测试：环形缓冲、序列取值与边界。"""

from __future__ import annotations

import pytest

from app.envelope import ParticipantState
from app.present.summary import CLOSED_KEY, LABEL_ORDER, summarize
from app.present.trend import DEFAULT_TREND_WINDOW, TrendBuffer
from common.perception_types import EmotionLabel, FinalState


def _summary(*labels: EmotionLabel, closed: int = 0):
    people: list[ParticipantState] = [
        ParticipantState(f"s{i:02d}", state=FinalState(label=label, timestamp=1.0, confidence=0.7))
        for i, label in enumerate(labels)
    ]
    people.extend(ParticipantState(f"c{i:02d}", closed=True) for i in range(closed))
    return summarize(people)


def test_empty_buffer() -> None:
    buffer = TrendBuffer()
    assert len(buffer) == 0
    assert buffer.samples() == []
    assert buffer.latest() is None
    assert buffer.window == DEFAULT_TREND_WINDOW


def test_window_must_be_positive() -> None:
    with pytest.raises(ValueError):
        TrendBuffer(0)
    with pytest.raises(ValueError):
        TrendBuffer(-3)


def test_push_returns_and_stores_sample() -> None:
    buffer = TrendBuffer(window=5)
    sample = buffer.push(_summary(EmotionLabel.FOCUSED, EmotionLabel.FOCUSED))
    assert sample.online_count == 2
    assert sample.by_label["focused"] == 2
    assert buffer.latest() is sample
    assert len(buffer) == 1


def test_ring_buffer_drops_oldest() -> None:
    buffer = TrendBuffer(window=2)
    buffer.push(_summary(EmotionLabel.FOCUSED))
    buffer.push(_summary(EmotionLabel.CONFUSED))
    buffer.push(_summary(EmotionLabel.DISTRACTED))
    assert len(buffer) == 2
    # 最旧的那帧（focused）已被挤掉
    assert buffer.series("focused") == [0, 0]
    assert buffer.series("confused") == [1, 0]
    assert buffer.series("distracted") == [0, 1]


def test_samples_are_oldest_to_newest() -> None:
    buffer = TrendBuffer(window=3)
    for label in (EmotionLabel.FOCUSED, EmotionLabel.CONFUSED, EmotionLabel.DISTRACTED):
        buffer.push(_summary(label))
    assert [sample.online_count for sample in buffer.samples()] == [1, 1, 1]
    assert buffer.series("focused") == [1, 0, 0]


def test_closed_series_reads_closed_count() -> None:
    buffer = TrendBuffer(window=3)
    buffer.push(_summary(EmotionLabel.FOCUSED, closed=2))
    buffer.push(_summary(closed=1))
    assert buffer.series(CLOSED_KEY) == [2, 1]


def test_unknown_key_yields_zeros_of_matching_length() -> None:
    buffer = TrendBuffer(window=3)
    buffer.push(_summary(EmotionLabel.FOCUSED))
    buffer.push(_summary(EmotionLabel.FOCUSED))
    assert buffer.series("not-a-label") == [0, 0]


def test_ratio_series_uses_each_frames_online_count() -> None:
    buffer = TrendBuffer(window=3)
    buffer.push(_summary(EmotionLabel.FOCUSED, EmotionLabel.FOCUSED))  # 2/2
    buffer.push(_summary(EmotionLabel.FOCUSED, EmotionLabel.CONFUSED))  # 1/2
    assert buffer.ratio_series("focused") == [1.0, 0.5]


def test_ratio_series_handles_zero_online() -> None:
    buffer = TrendBuffer(window=2)
    buffer.push(_summary())  # 0 人在线
    assert buffer.ratio_series("focused") == [0.0]


def test_series_covers_every_label_key() -> None:
    buffer = TrendBuffer(window=2)
    buffer.push(_summary(EmotionLabel.FOCUSED))
    for key in LABEL_ORDER:
        assert len(buffer.series(key)) == 1


def test_clear_empties_the_buffer() -> None:
    buffer = TrendBuffer(window=3)
    buffer.push(_summary(EmotionLabel.FOCUSED))
    buffer.clear()
    assert len(buffer) == 0
    assert buffer.series("focused") == []
