"""``common.input_spec``（表 B）的测试。

⚠️ 本文件**刻意不 import numpy**：CI 的 4 个 job 只装 `requirements-dev.txt`，里面没有
numpy（这是刻意的，见 `requirements-dev.txt` 的注释）。所以这里用鸭子类型的替身对象
来构造「帧」，而不是真的造一个 ``np.zeros``。
"""

from __future__ import annotations

import pytest

from common import input_spec
from common.input_spec import (
    FRAME_CHANNELS,
    FRAME_COLOR_SPACE,
    FRAME_DTYPE,
    FRAME_SHAPE_ORDER,
    FRAME_VALUE_RANGE,
    MIN_FRAME_SIDE,
    check_frame,
    require_frame,
    roi_size,
    sequence_len,
    sequence_span_seconds,
    target_fps,
)


class _DType:
    """模拟 ``numpy.dtype``：检查器只读它的 ``.name``。"""

    def __init__(self, name: str) -> None:
        self.name = name


class _Frame:
    """模拟 ``numpy.ndarray`` 的最小替身：只有 ``.shape`` 与 ``.dtype``。"""

    def __init__(self, shape: tuple[int, ...], dtype: object = "uint8") -> None:
        self.shape = shape
        self.dtype = _DType(dtype) if isinstance(dtype, str) else dtype


class _ShapeOnly:
    """有 shape 但没有 dtype —— 覆盖「缺少 .dtype」分支。"""

    def __init__(self, shape: tuple[int, ...]) -> None:
        self.shape = shape


# ---------------------------------------------------------------------------
# 常量本身
# ---------------------------------------------------------------------------


def test_declared_constants_are_the_documented_ones() -> None:
    assert FRAME_COLOR_SPACE == "BGR"
    assert FRAME_DTYPE == "uint8"
    assert FRAME_VALUE_RANGE == (0, 255)
    assert FRAME_SHAPE_ORDER == "HWC"
    assert FRAME_CHANNELS == 3
    assert MIN_FRAME_SIDE == 64


# ---------------------------------------------------------------------------
# 合格帧
# ---------------------------------------------------------------------------


def test_valid_frame_has_no_problems() -> None:
    assert check_frame(_Frame((480, 640, 3))) == ()


def test_valid_frame_passes_require_frame() -> None:
    require_frame(_Frame((480, 640, 3)))


def test_shortest_allowed_side_is_accepted() -> None:
    """刚好等于下限应当合格 —— 边界值不能被写成「严格小于」。"""
    assert check_frame(_Frame((MIN_FRAME_SIDE, MIN_FRAME_SIDE, 3))) == ()


# ---------------------------------------------------------------------------
# None 哨兵
# ---------------------------------------------------------------------------


def test_none_is_reported_but_is_a_known_case() -> None:
    """协议允许 ``None`` 出现（单测/mock），但检查器必须把它报出来。"""
    problems = check_frame(None)
    assert len(problems) == 1
    assert "None" in problems[0]


def test_none_is_rejected_by_require_frame() -> None:
    with pytest.raises(ValueError, match="frame is None"):
        require_frame(None)


# ---------------------------------------------------------------------------
# 形状与通道
# ---------------------------------------------------------------------------


def test_object_without_shape_is_reported_with_its_type() -> None:
    problems = check_frame(object())
    assert len(problems) == 1
    assert "缺少 .shape" in problems[0]
    assert "object" in problems[0]


def test_gray_frame_is_reported() -> None:
    problems = check_frame(_Frame((480, 640)))
    assert len(problems) == 1
    assert "维度应为 3" in problems[0]


def test_four_channel_frame_is_reported() -> None:
    """BGRA（例如带 alpha 的采集源）必须被拦住：通道序会整体错位。"""
    problems = check_frame(_Frame((480, 640, 4)))
    assert any("通道数应为 3" in problem for problem in problems)


def test_too_small_height_is_reported() -> None:
    problems = check_frame(_Frame((32, 640, 3)))
    assert len(problems) == 1
    assert "短边" in problems[0]


def test_too_small_width_is_reported() -> None:
    """高度合格、宽度过小 —— 覆盖短边判断的第二个操作数。"""
    problems = check_frame(_Frame((480, 32, 3)))
    assert len(problems) == 1
    assert "短边" in problems[0]


def test_shape_and_dtype_problems_accumulate() -> None:
    """多条问题应一次性全部报出，而不是只报第一条。"""
    problems = check_frame(_Frame((32, 32, 4), dtype="float32"))
    assert len(problems) == 3


# ---------------------------------------------------------------------------
# dtype
# ---------------------------------------------------------------------------


def test_missing_dtype_is_reported() -> None:
    problems = check_frame(_ShapeOnly((480, 640, 3)))
    assert len(problems) == 1
    assert "缺少 .dtype" in problems[0]


def test_dtype_without_name_attribute_is_reported_as_missing() -> None:
    """``.dtype`` 存在但拿不到名字（无 ``.name``）时按「缺 dtype」处理，不做隐式转换。"""
    problems = check_frame(_Frame((480, 640, 3), dtype=object()))
    assert len(problems) == 1
    assert "缺少 .dtype" in problems[0]


def test_wrong_dtype_is_reported_with_the_actual_value() -> None:
    problems = check_frame(_Frame((480, 640, 3), dtype="float32"))
    assert len(problems) == 1
    assert "实际 float32" in problems[0]


# ---------------------------------------------------------------------------
# require_frame
# ---------------------------------------------------------------------------


def test_require_frame_joins_all_problems_into_one_message() -> None:
    with pytest.raises(ValueError) as excinfo:
        require_frame(_Frame((32, 32, 4), dtype="float32"))
    message = str(excinfo.value)
    assert "输入规格" in message
    assert "；" in message


# ---------------------------------------------------------------------------
# 与 configs/thresholds.yaml 的读取接口
# ---------------------------------------------------------------------------

#: 刻意**不**把 16 / 15 / 224 钉进断言 —— 调参不需要三方协商，
#: 测试若跟着一起红，就等于给调参上了锁。这里只断言「关系」与「合理性」。


def test_config_accessors_return_sane_values() -> None:
    assert target_fps() > 0
    assert sequence_len() >= 2
    assert roi_size() >= 32


def test_sequence_span_is_sequence_len_over_target_fps() -> None:
    assert sequence_span_seconds() == pytest.approx(sequence_len() / target_fps())


def test_sequence_span_stays_below_five_seconds() -> None:
    """跨度是行为窗口的「视野」；超过几秒就意味着它在用很久以前的姿态判现在。"""
    assert 0.0 < sequence_span_seconds() < 5.0


def test_sequence_span_guards_against_zero_fps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(input_spec, "target_fps", lambda: 0.0)
    assert sequence_span_seconds() == 0.0
