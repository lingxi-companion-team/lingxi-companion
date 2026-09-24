"""``common.label_mapping``（表 A）的测试：表结构自洽 + 输出是合法 ``prob_dist``。

这里测的是**骨架的结构正确性**（行和为 1、键集固定、输出满足契约），
**不是**映射系数是否合理 —— 系数合理性要靠标注规范与数据论证，不是单元测试能覆盖的。
所以下面刻意不去断言「happy 应该映射到 focused」这类内容，只断言机器可判的性质。
"""

from __future__ import annotations

import pytest

from common.label_mapping import (
    BEHAVIOR_SOURCE_LABELS,
    BEHAVIOR_TO_EMOTION,
    EXPRESSION_SOURCE_LABELS,
    EXPRESSION_TO_EMOTION,
    TABLES,
    dominant,
    table_row_sums,
    to_prob_dist,
    to_result,
    validate_tables,
)
from common.perception_types import (
    AGENT_BEHAVIOR,
    AGENT_EXPRESSION,
    EMOTION_LABELS,
    EmotionLabel,
    PerceptionResult,
)

ALL_TABLES = (EXPRESSION_TO_EMOTION, BEHAVIOR_TO_EMOTION)
ROW_CASES = [(EXPRESSION_TO_EMOTION, source) for source in sorted(EXPRESSION_TO_EMOTION)]
ROW_CASES += [(BEHAVIOR_TO_EMOTION, source) for source in sorted(BEHAVIOR_TO_EMOTION)]


# ---------------------------------------------------------------------------
# 表结构本身
# ---------------------------------------------------------------------------


def test_shipped_tables_pass_validation() -> None:
    """出厂表必须自洽 —— 行和、键集、取值范围一次校验完。"""
    validate_tables()


def test_tables_registry_covers_both_agents() -> None:
    assert set(TABLES) == {"expression", "behavior"}
    assert TABLES["expression"] is EXPRESSION_TO_EMOTION
    assert TABLES["behavior"] is BEHAVIOR_TO_EMOTION


@pytest.mark.parametrize("table", ALL_TABLES)
def test_every_row_has_exactly_the_three_emotion_labels(table: dict) -> None:
    for source, row in table.items():
        assert set(row) == set(EMOTION_LABELS), source
        assert EmotionLabel.UNKNOWN not in row, "UNKNOWN 是哨兵，不得进入映射表"


@pytest.mark.parametrize("table", ALL_TABLES)
def test_every_row_sums_to_one(table: dict) -> None:
    for source, total in table_row_sums(table).items():
        assert total == pytest.approx(1.0), source


def test_declared_source_labels_match_table_keys() -> None:
    """声明的类别清单与表键必须一致，否则「漏填一行」不会被发现。"""
    assert sorted(EXPRESSION_SOURCE_LABELS) == sorted(EXPRESSION_TO_EMOTION)
    assert sorted(BEHAVIOR_SOURCE_LABELS) == sorted(BEHAVIOR_TO_EMOTION)


def test_behavior_covers_the_actions_the_spec_cares_about() -> None:
    """托腮 / 低头 / 前倾是本项目的三个核心行为（见算法文档 §2）。"""
    for action in ("hand_on_face", "head_down", "lean_forward"):
        assert action in BEHAVIOR_TO_EMOTION


# ---------------------------------------------------------------------------
# validate_tables 的拒绝路径（校验器本身也要被校验）
# ---------------------------------------------------------------------------


def test_validate_rejects_empty_table() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        validate_tables({"broken": {}})


def test_validate_rejects_missing_key() -> None:
    bad = {"x": {EmotionLabel.FOCUSED: 0.5, EmotionLabel.CONFUSED: 0.5}}
    with pytest.raises(ValueError, match="missing"):
        validate_tables({"broken": bad})


def test_validate_rejects_extra_key() -> None:
    bad = {
        "x": {
            EmotionLabel.FOCUSED: 0.4,
            EmotionLabel.CONFUSED: 0.3,
            EmotionLabel.DISTRACTED: 0.3,
            EmotionLabel.UNKNOWN: 0.0,
        }
    }
    with pytest.raises(ValueError, match="unexpected"):
        validate_tables({"broken": bad})


def test_validate_rejects_out_of_range_value() -> None:
    bad = {
        "x": {
            EmotionLabel.FOCUSED: 1.2,
            EmotionLabel.CONFUSED: -0.1,
            EmotionLabel.DISTRACTED: -0.1,
        }
    }
    with pytest.raises(ValueError, match=r"must be in \[0, 1\]"):
        validate_tables({"broken": bad})


def test_validate_rejects_row_that_does_not_sum_to_one() -> None:
    bad = {
        "x": {
            EmotionLabel.FOCUSED: 0.5,
            EmotionLabel.CONFUSED: 0.3,
            EmotionLabel.DISTRACTED: 0.1,
        }
    }
    with pytest.raises(ValueError, match="must sum to 1"):
        validate_tables({"broken": bad})


# ---------------------------------------------------------------------------
# to_prob_dist
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("table", "source"), ROW_CASES)
def test_single_source_scores_reproduce_that_row(table: dict, source: str) -> None:
    """只给一个上游类别时，结果应等于该行系数（归一化不改变已归一化的行）。"""
    dist = to_prob_dist({source: 1.0}, table)
    expected = table[source]
    for label in EMOTION_LABELS:
        assert dist[label] == pytest.approx(expected[label])


def test_mapping_is_scale_invariant() -> None:
    """入参是否归一化不影响结果 —— 既接受概率也接受相对 logits。

    只保证到浮点精度：``0.7/0.3`` 与 ``7.0/3.0`` 的比值在二进制里并不完全相等，
    所以逐项用 ``approx`` 比较，而不是直接比 dict。
    """
    as_probs = to_prob_dist({"happy": 0.7, "sad": 0.3}, EXPRESSION_TO_EMOTION)
    as_logits = to_prob_dist({"happy": 7.0, "sad": 3.0}, EXPRESSION_TO_EMOTION)
    for label in EMOTION_LABELS:
        assert as_probs[label] == pytest.approx(as_logits[label])


def test_unknown_source_label_is_ignored_not_fatal() -> None:
    """模型换了标签集时不能把整路打挂（``infer()`` 禁止抛异常）。"""
    dist = to_prob_dist({"happy": 1.0, "brand_new_label": 9.0}, EXPRESSION_TO_EMOTION)
    assert dist == to_prob_dist({"happy": 1.0}, EXPRESSION_TO_EMOTION)


def test_non_positive_scores_do_not_vote() -> None:
    zero = to_prob_dist({"happy": 0.0}, EXPRESSION_TO_EMOTION)
    negative = to_prob_dist({"happy": -3.0}, EXPRESSION_TO_EMOTION)
    assert zero == negative
    assert sum(zero.values()) == pytest.approx(1.0)


def test_empty_scores_fall_back_to_uniform() -> None:
    dist = to_prob_dist({}, EXPRESSION_TO_EMOTION)
    assert set(dist) == set(EMOTION_LABELS)
    assert dist[EmotionLabel.FOCUSED] == pytest.approx(1 / 3)
    assert sum(dist.values()) == pytest.approx(1.0)


def test_explicit_fallback_is_normalized() -> None:
    dist = to_prob_dist(
        {},
        EXPRESSION_TO_EMOTION,
        fallback={
            EmotionLabel.FOCUSED: 1.0,
            EmotionLabel.CONFUSED: 2.0,
            EmotionLabel.DISTRACTED: 1.0,
        },
    )
    assert dist[EmotionLabel.CONFUSED] == pytest.approx(0.5)
    assert sum(dist.values()) == pytest.approx(1.0)


def test_degenerate_fallback_falls_back_to_uniform() -> None:
    """全零的 fallback 也救不了场时，仍要返回一个合法分布而不是 NaN。"""
    dist = to_prob_dist(
        {},
        EXPRESSION_TO_EMOTION,
        fallback={label: 0.0 for label in EMOTION_LABELS},
    )
    assert dist[EmotionLabel.FOCUSED] == pytest.approx(1 / 3)


def test_returned_dict_is_a_fresh_copy() -> None:
    """调用方改返回值不能污染映射表。"""
    dist = to_prob_dist({"happy": 1.0}, EXPRESSION_TO_EMOTION)
    dist[EmotionLabel.FOCUSED] = 0.0
    assert EXPRESSION_TO_EMOTION["happy"][EmotionLabel.FOCUSED] == pytest.approx(0.80)


# ---------------------------------------------------------------------------
# dominant
# ---------------------------------------------------------------------------


def test_dominant_picks_the_maximum() -> None:
    dist = {
        EmotionLabel.FOCUSED: 0.2,
        EmotionLabel.CONFUSED: 0.7,
        EmotionLabel.DISTRACTED: 0.1,
    }
    assert dominant(dist) == (EmotionLabel.CONFUSED, pytest.approx(0.7))


def test_dominant_breaks_ties_in_canonical_order() -> None:
    uniform = {label: 1 / 3 for label in EMOTION_LABELS}
    assert dominant(uniform)[0] is EMOTION_LABELS[0]


# ---------------------------------------------------------------------------
# to_result：与 PerceptionResult 契约的一致性
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("agent_id", "table"),
    [(AGENT_EXPRESSION, EXPRESSION_TO_EMOTION), (AGENT_BEHAVIOR, BEHAVIOR_TO_EMOTION)],
)
def test_to_result_is_accepted_by_the_contract(agent_id: str, table: dict) -> None:
    """每个上游类别都要能装配出 ``PerceptionResult`` 而不触发契约校验。"""
    for source in table:
        result = to_result(agent_id, {source: 1.0}, table, ts=1.5, frame_id=7)
        assert isinstance(result, PerceptionResult)
        assert result.label is not EmotionLabel.UNKNOWN
        assert set(result.prob_dist) == set(EMOTION_LABELS)
        assert sum(result.prob_dist.values()) == pytest.approx(1.0)
        assert result.prob == pytest.approx(result.prob_dist[result.label])
        assert result.ts == 1.5
        assert result.frame_id == 7


def test_to_result_defaults_confidence_to_the_winning_probability() -> None:
    result = to_result(AGENT_EXPRESSION, {"happy": 1.0}, EXPRESSION_TO_EMOTION)
    assert result.label is EmotionLabel.FOCUSED
    assert result.confidence == pytest.approx(result.prob)


def test_to_result_accepts_an_explicit_confidence() -> None:
    """模型另有一套置信度时（如人脸检测分数）应能覆盖默认值。"""
    result = to_result(
        AGENT_EXPRESSION,
        {"happy": 1.0},
        EXPRESSION_TO_EMOTION,
        confidence=0.42,
    )
    assert result.confidence == pytest.approx(0.42)
    assert result.prob == pytest.approx(0.80)


def test_to_result_on_empty_scores_stays_contract_valid() -> None:
    result = to_result(AGENT_BEHAVIOR, {}, BEHAVIOR_TO_EMOTION)
    assert sum(result.prob_dist.values()) == pytest.approx(1.0)
    assert result.prob == pytest.approx(result.prob_dist[result.label])
