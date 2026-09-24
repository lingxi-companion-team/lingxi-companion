"""环境智能体单测（零第三方依赖，与 CI 环境一致）。

为什么用「假帧」而不是 numpy
--------------------------
CI 只装 ``requirements-dev.txt``（无 numpy），而本模块刻意做成零依赖 ——
测试必须能在同一个环境里跑，所以帧用「嵌套列表 + ``shape`` / ``dtype`` 外壳」
的替身来构造。这同时也验证了模块**确实只依赖鸭子类型**：如果哪天有人偷偷
``import numpy``，这些测试会直接失败。

像素取值约定：BGR（与 :mod:`common.input_spec` 的 ``frame`` 规格一致）。
"""

from __future__ import annotations

import dataclasses
import random

import pytest

from agents.env import RuleBasedEnvAgent as ExportedAgent
from agents.env.agent import (
    MIN_GRID_SIDE,
    SAMPLE_COLS,
    SAMPLE_ROWS,
    EnvMetrics,
    RuleBasedEnvAgent,
    _blocked_ratio,
    evaluate_luma,
    laplacian_variance,
    sample_luma,
)
from common.config import DEFAULT_E0, get
from common.perception_types import AGENT_ENV, EnvContext

#: 采样网格边长。
GRID = SAMPLE_ROWS

#: 测试用帧边长。必须 ≥ ``common.input_spec.MIN_FRAME_SIDE``（64），
#: 否则帧会在 ``assess()`` 的第一道门就被判为不合格、走不到度量逻辑。
FRAME_SIDE = 64


# ---------------------------------------------------------------------------
# 帧替身与图像生成器（全部确定性，不使用随机种子以外的不确定源）
# ---------------------------------------------------------------------------


class _DType:
    """``.dtype.name`` 的最小替身（``check_frame`` 只读这个属性）。"""

    def __init__(self, name: str = "uint8") -> None:
        self.name = name


class FakeFrame:
    """符合 ``common.input_spec`` 规格的最小帧替身，**不使用 numpy**。"""

    def __init__(self, pixels: list[list[list[int]]], *, channels: int = 3, dtype: str = "uint8"):
        self._pixels = pixels
        height = len(pixels)
        width = len(pixels[0]) if pixels else 0
        self.shape = (height, width, channels)
        self.dtype = _DType(dtype)

    def __getitem__(self, index: int) -> list[list[int]]:
        return self._pixels[index]


class ExplodingFrame(FakeFrame):
    """声明形状合法、但一取像素就抛异常的帧：用于验证降级而不是崩溃。"""

    def __getitem__(self, index: int) -> list[list[int]]:
        raise RuntimeError("boom")


def _pixels(grid: list[list[float]], channels: int = 3) -> list[list[list[int]]]:
    return [[[int(value)] * channels for value in row] for row in grid]


def _frame(grid: list[list[float]], *, channels: int = 3, dtype: str = "uint8") -> FakeFrame:
    return FakeFrame(_pixels(grid, channels), channels=channels, dtype=dtype)


def _uniform(value: float, side: int = FRAME_SIDE) -> list[list[float]]:
    return [[float(value)] * side for _ in range(side)]


def _noise(seed: int, side: int = FRAME_SIDE, low: int = 0, high: int = 255) -> list[list[float]]:
    rng = random.Random(seed)
    return [[float(rng.randint(low, high)) for _ in range(side)] for _ in range(side)]


def _box_blur(grid: list[list[float]], passes: int = 1) -> list[list[float]]:
    """3×3 盒式模糊，模拟失焦（低频化）。用纯 Python 实现以免依赖 numpy。"""
    side = len(grid)
    current = [row[:] for row in grid]
    for _ in range(passes):
        blurred = [[0.0] * side for _ in range(side)]
        for y in range(side):
            for x in range(side):
                total = 0.0
                count = 0
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        ny, nx = y + dy, x + dx
                        if 0 <= ny < side and 0 <= nx < side:
                            total += current[ny][nx]
                            count += 1
                blurred[y][x] = total / count
        current = blurred
    return current


def _blob(seed: int, level: float, blob_side: int, side: int = FRAME_SIDE) -> list[list[float]]:
    """噪声底 + 中央一块纯色方块（模拟贴近镜头的物体）。"""
    grid = _noise(seed, side)
    offset = (side - blob_side) // 2
    for y in range(offset, offset + blob_side):
        for x in range(offset, offset + blob_side):
            grid[y][x] = float(level)
    return grid


def _expected_neutral_e() -> float:
    """中性 $E$ 的期望值：与融合层/基类同源，从配置读而不是写死。"""
    return float(get("weights", "e0", default=DEFAULT_E0))


# ---------------------------------------------------------------------------
# 常量与模块导出
# ---------------------------------------------------------------------------


def test_exported_from_package() -> None:
    """``agents.env`` 包级导出必须指向同一个类（外部只 import 包也能用）。"""
    assert ExportedAgent is RuleBasedEnvAgent


def test_sample_grid_matches_planned_tinycnn_input() -> None:
    """32×32 是刻意选的：与将来 TinyCNN 的输入尺寸一致，换模型时采样层可留用。"""
    assert (SAMPLE_COLS, SAMPLE_ROWS) == (32, 32)


def test_min_grid_side_covers_laplacian_and_block_math() -> None:
    """短边下限必须同时满足拉普拉斯（3）与遮挡分块（4）的最小需求。"""
    assert MIN_GRID_SIDE >= 4


def test_env_metrics_is_frozen() -> None:
    """度量结果必须是不可变的 —— 它会被搬进 ``EnvContext``，改动应当是显式动作。"""
    metrics = EnvMetrics(brightness=0.5, blur=0.5, occlusion=0.0, env_score=0.6)
    with pytest.raises(dataclasses.FrozenInstanceError):
        metrics.blur = 0.1  # type: ignore[misc]


# ---------------------------------------------------------------------------
# sample_luma
# ---------------------------------------------------------------------------


def test_sample_luma_uniform_frame_keeps_value() -> None:
    grid = sample_luma(_frame(_uniform(100.0)))
    assert len(grid) == SAMPLE_ROWS
    assert all(len(row) == SAMPLE_COLS for row in grid)
    assert all(value == pytest.approx(100.0) for row in grid for value in row)


def test_sample_luma_applies_bt601_weights() -> None:
    """灰度权重必须按 BGR 顺序取：纯蓝 ≈ 0.114、纯绿 ≈ 0.587、纯红 ≈ 0.299。

    整帧涂成同一种颜色，避免依赖「采样点落在哪一行哪一列」——
    采样是块中心取值（见 ``test_sample_luma_takes_block_centre_pixel``），
    只改某一个像素会让这条测试变成对采样位置的间接断言。
    """
    for channel, expected in ((0, 0.114), (1, 0.587), (2, 0.299)):
        pixels = [[[0, 0, 0] for _ in range(FRAME_SIDE)] for _ in range(FRAME_SIDE)]
        for row in pixels:
            for pixel in row:
                pixel[channel] = 255
        grid = sample_luma(FakeFrame(pixels))
        assert grid[0][0] == pytest.approx(255 * expected, abs=0.01)


def test_sample_luma_honours_custom_grid_size() -> None:
    grid = sample_luma(_frame(_uniform(10.0)), cols=4, rows=8)
    assert len(grid) == 8
    assert all(len(row) == 4 for row in grid)


def test_sample_luma_takes_block_centre_pixel() -> None:
    """64×64 降到 32×32 时取的是奇数行/奇数列（块中心），不是左上角。"""
    pixels = [[[0, 0, 0] for _ in range(FRAME_SIDE)] for _ in range(FRAME_SIDE)]
    pixels[1][1] = [255, 255, 255]
    assert sample_luma(FakeFrame(pixels))[0][0] == pytest.approx(255.0)


def test_sample_luma_clamps_index_for_small_frames() -> None:
    """帧比网格还小时 ``min()`` 兜住索引，不越界（不负责判质量，只保证不崩）。"""
    grid = sample_luma(_frame(_uniform(7.0, side=2)), cols=4, rows=4)
    assert len(grid) == 4
    assert all(value == pytest.approx(7.0) for row in grid for value in row)


# ---------------------------------------------------------------------------
# laplacian_variance
# ---------------------------------------------------------------------------


def test_laplacian_variance_of_empty_grid_is_zero() -> None:
    assert laplacian_variance([]) == 0.0


@pytest.mark.parametrize("grid", [[[1.0, 2.0]], [[1.0, 2.0], [3.0, 4.0]]])
def test_laplacian_variance_of_too_small_grid_is_zero(grid: list[list[float]]) -> None:
    assert laplacian_variance(grid) == 0.0


def test_laplacian_variance_of_uniform_grid_is_zero() -> None:
    assert laplacian_variance(_uniform(128.0, side=8)) == 0.0


def test_laplacian_variance_of_linear_ramp_is_zero() -> None:
    """线性斜坡的二阶导为 0 —— 这是「平滑但清晰」的画面，不该被当成模糊。"""
    ramp = [[float(x) for x in range(8)] for _ in range(8)]
    assert laplacian_variance(ramp) == pytest.approx(0.0)


def test_laplacian_variance_of_single_impulse_matches_analytic() -> None:
    """5×5 全零 + 中心 1：内部 9 个拉普拉斯值是 [-4, 1, 1, 1, 1, 0, 0, 0, 0]，
    均值 0、方差 20/9 —— 用手算值钉住核的方向与符号。"""
    grid = [[0.0] * 5 for _ in range(5)]
    grid[2][2] = 1.0
    assert laplacian_variance(grid) == pytest.approx(20.0 / 9.0)


def test_laplacian_variance_of_noise_is_large() -> None:
    assert laplacian_variance(_noise(1, side=32)) > 1000.0


# ---------------------------------------------------------------------------
# _blocked_ratio
# ---------------------------------------------------------------------------


def test_blocked_ratio_returns_zero_for_grid_smaller_than_block() -> None:
    """网格连一个完整块都凑不出时返回 0（宁可漏报，不可臆造遮挡）。"""
    assert _blocked_ratio([[1.0, 2.0], [3.0, 4.0]]) == 0.0


def test_blocked_ratio_ignores_noisy_blocks() -> None:
    """有纹理的块不是遮挡 —— 噪声图应当报 0。"""
    assert _blocked_ratio(_noise(3, side=GRID)) == 0.0


def test_blocked_ratio_ignores_flat_mid_grey() -> None:
    """中灰平坦块**刻意不计**遮挡：那是「对着白墙」，该由亮度/清晰度因子处理。"""
    assert _blocked_ratio(_uniform(128.0, side=GRID)) == 0.0


@pytest.mark.parametrize("level", [10.0, 250.0])
def test_blocked_ratio_counts_flat_extreme_blocks(level: float) -> None:
    """过暗（剪影）与过曝（灯直照）的平坦块都算遮挡。"""
    assert _blocked_ratio(_blob(5, level, blob_side=20, side=GRID)) > 0.0


def test_blocked_ratio_grows_with_blob_size() -> None:
    small = _blocked_ratio(_blob(5, 10.0, blob_side=8, side=GRID))
    large = _blocked_ratio(_blob(5, 10.0, blob_side=20, side=GRID))
    assert small < large


# ---------------------------------------------------------------------------
# evaluate_luma
# ---------------------------------------------------------------------------


def test_evaluate_luma_empty_grid_returns_neutral() -> None:
    metrics = evaluate_luma([])
    assert metrics.brightness == pytest.approx(0.5)
    assert metrics.blur == pytest.approx(0.5)
    assert metrics.occlusion == 0.0
    assert metrics.env_score == pytest.approx(_expected_neutral_e())


def test_evaluate_luma_small_grid_returns_neutral() -> None:
    metrics = evaluate_luma([[1.0] * 4 for _ in range(4)])
    assert metrics.env_score == pytest.approx(_expected_neutral_e())


def test_evaluate_luma_brightness_is_mean_luma_over_255() -> None:
    metrics = evaluate_luma(_uniform(128.0, side=GRID))
    assert metrics.brightness == pytest.approx(128 / 255, abs=1e-6)


def test_evaluate_luma_open_mid_range_keeps_brightness_factor_at_one() -> None:
    """亮度落在 [brightness_min, brightness_max] 内时亮度因子为 1。"""
    metrics = evaluate_luma(_noise(11, side=GRID, low=90, high=160))
    assert 0.25 < metrics.brightness < 0.95


def test_evaluate_luma_dark_and_bright_both_drop_score() -> None:
    dark = evaluate_luma(_noise(11, side=GRID, low=0, high=40))
    bright = evaluate_luma(_noise(11, side=GRID, low=235, high=255))
    normal = evaluate_luma(_noise(11, side=GRID, low=90, high=160))
    assert dark.env_score < normal.env_score
    assert bright.env_score < normal.env_score


def test_evaluate_luma_negative_luma_is_clamped_to_zero_brightness() -> None:
    metrics = evaluate_luma([[-40.0] * GRID for _ in range(GRID)])
    assert metrics.brightness == 0.0


def test_evaluate_luma_dark_excess_is_clamped_at_full_penalty() -> None:
    """远低于亮度下限时过暗程度被夹到 1，$E$ 归零而不是变成负数。"""
    metrics = evaluate_luma([[-1000.0] * GRID for _ in range(GRID)])
    assert metrics.env_score == 0.0


def test_evaluate_luma_metrics_stay_in_unit_range() -> None:
    for grid in (
        _noise(2, side=GRID),
        _uniform(0.0, side=GRID),
        _uniform(255.0, side=GRID),
        _box_blur(_noise(2, side=GRID), 3),
        _blob(2, 10.0, blob_side=20, side=GRID),
    ):
        metrics = evaluate_luma(grid)
        for value in (metrics.brightness, metrics.blur, metrics.occlusion, metrics.env_score):
            assert 0.0 <= value <= 1.0


def test_evaluate_luma_uniform_frame_is_maximally_blurry() -> None:
    """完全没有细节 ⇒ blur 到顶、$E$ 归零（表情通道不可能可信）。"""
    metrics = evaluate_luma(_uniform(128.0, side=GRID))
    assert metrics.blur == pytest.approx(1.0, abs=1e-6)
    assert metrics.env_score == pytest.approx(0.0, abs=1e-6)


def test_evaluate_luma_sharp_noise_is_not_blurry() -> None:
    metrics = evaluate_luma(_noise(7, side=GRID))
    assert metrics.blur < 0.05
    assert metrics.env_score > 0.95


def test_evaluate_luma_blur_is_monotonic_in_blurring() -> None:
    """模糊越多 ⇒ blur 单调升高 ⇒ $E$ 单调下降。这是本模块最核心的行为契约。"""
    base = _noise(7, side=GRID)
    series = [evaluate_luma(base if n == 0 else _box_blur(base, n)) for n in (0, 1, 2, 3, 5, 8)]
    blurs = [item.blur for item in series]
    scores = [item.env_score for item in series]
    assert blurs == sorted(blurs)
    assert len(set(blurs)) == len(blurs), "各档模糊应当给出互不相同的 blur 值"
    assert scores == sorted(scores, reverse=True)


def test_evaluate_luma_raising_tolerance_raises_score() -> None:
    """容忍线放宽 ⇒ 同一画面应当**更可信**。

    这是个方向性契约：若把容忍线直接当分母，方向会反过来（调宽反而更严格），
    属于实现里必须避免的错误 —— 用测试钉死。
    """
    blurred = _box_blur(_noise(7, side=GRID), 2)
    scores = [evaluate_luma(blurred, blur_tolerance=tol).env_score for tol in (0.2, 0.4, 0.8)]
    assert scores == sorted(scores)
    # blur 是观测量，不随容忍线漂移 —— 否则不同配置下的数字无法互相比较。
    blurs = [evaluate_luma(blurred, blur_tolerance=tol).blur for tol in (0.2, 0.4, 0.8)]
    assert len(set(blurs)) == 1


def test_evaluate_luma_zero_tolerance_kills_clarity() -> None:
    """容忍线为 0 表示「一点模糊都不接受」⇒ 清晰度因子归零、$E$ 归零。"""
    metrics = evaluate_luma(_box_blur(_noise(7, side=GRID), 2), blur_tolerance=0.0)
    assert metrics.env_score == 0.0


def test_evaluate_luma_occlusion_is_not_folded_into_score() -> None:
    """遮挡权重为 0（配置约定）时，**遮挡不得影响 $E$** —— 否则会与融合层
    §3.4 的补偿重复计入一次，补偿强度失控。"""
    occluded = evaluate_luma(_blob(5, 10.0, blob_side=20, side=GRID))
    assert occluded.occlusion > 0.0
    assert occluded.env_score > 0.9  # 画面本身清晰明亮 → E 仍高，遮挡只上报


def test_evaluate_luma_occlusion_weight_applies_when_set() -> None:
    """权重 > 0 时该配置项确实生效（否则它会变成「没有读取方的配置项」）。"""
    grid = _blob(5, 10.0, blob_side=20, side=GRID)
    plain = evaluate_luma(grid)
    weighted = evaluate_luma(grid, occlusion_weight=1.0)
    assert weighted.env_score < plain.env_score
    assert weighted.occlusion == pytest.approx(plain.occlusion)


def test_evaluate_luma_occlusion_weight_is_clamped() -> None:
    """权重 > 1 被夹到 1，$E$ 不会翻负。"""
    grid = _blob(5, 10.0, blob_side=20, side=GRID)
    metrics = evaluate_luma(grid, occlusion_weight=5.0)
    assert 0.0 <= metrics.env_score <= 1.0


def test_evaluate_luma_is_deterministic() -> None:
    grid = _noise(13, side=GRID)
    assert evaluate_luma(grid) == evaluate_luma(grid)


# ---------------------------------------------------------------------------
# RuleBasedEnvAgent
# ---------------------------------------------------------------------------


def test_agent_reads_thresholds_from_config() -> None:
    agent = RuleBasedEnvAgent()
    assert agent.blur_tolerance == pytest.approx(
        float(get("env_agent", "blur_tolerance", default=0.4))
    )
    assert agent.brightness_min == pytest.approx(
        float(get("env_agent", "brightness_min", default=0.25))
    )
    assert agent.brightness_max == pytest.approx(
        float(get("env_agent", "brightness_max", default=0.95))
    )
    assert agent.occlusion_weight == pytest.approx(
        float(get("env_agent", "occlusion_weight", default=0.0))
    )


def test_agent_accepts_explicit_overrides() -> None:
    """标定时要能临时改阈值，而不必去动 ``configs/``（那是接口变更流程）。"""
    agent = RuleBasedEnvAgent(
        blur_tolerance=0.7,
        brightness_min=0.1,
        brightness_max=0.9,
        occlusion_weight=0.5,
    )
    assert agent.blur_tolerance == pytest.approx(0.7)
    assert agent.brightness_min == pytest.approx(0.1)
    assert agent.brightness_max == pytest.approx(0.9)
    assert agent.occlusion_weight == pytest.approx(0.5)


def test_agent_id_is_the_frozen_constant() -> None:
    assert RuleBasedEnvAgent().agent_id == AGENT_ENV


def test_warmup_is_a_noop_and_idempotent() -> None:
    agent = RuleBasedEnvAgent()
    assert agent.warmup() is None
    assert agent.warmup() is None


def test_assess_returns_env_context_for_valid_frame() -> None:
    agent = RuleBasedEnvAgent()
    context = agent.assess(_frame(_noise(7)), ts=1.25, frame_id=7)
    assert isinstance(context, EnvContext)
    assert context.ts == pytest.approx(1.25)
    assert context.frame_id == 7
    assert 0.0 <= context.env_score <= 1.0
    assert context.env_score > 0.9  # 清晰且亮度正常


def test_assess_reports_low_score_for_blurred_frame() -> None:
    agent = RuleBasedEnvAgent()
    blurred = agent.assess(_frame(_box_blur(_noise(7), 5)), ts=0.0, frame_id=1)
    sharp = agent.assess(_frame(_noise(7)), ts=0.0, frame_id=1)
    assert blurred.env_score < sharp.env_score


def test_assess_none_frame_degrades_to_neutral() -> None:
    """``None`` 是无帧哨兵，协议允许出现 —— 必须降级，不得抛异常。"""
    context = RuleBasedEnvAgent().assess(None, ts=2.0, frame_id=9)
    assert context.env_score == pytest.approx(_expected_neutral_e())
    assert context.brightness == pytest.approx(0.5)
    assert context.occlusion == 0.0
    assert (context.ts, context.frame_id) == (2.0, 9)


@pytest.mark.parametrize(
    "frame",
    [
        "not a frame",
        FakeFrame([], channels=3),
        _frame(_uniform(128.0), channels=4),  # 通道数不符
        _frame(_uniform(128.0, side=32)),  # 短边低于规格下限
        _frame(_uniform(128.0), dtype="float32"),  # dtype 不符
    ],
)
def test_assess_rejects_frames_that_violate_input_spec(frame: object) -> None:
    context = RuleBasedEnvAgent().assess(frame, ts=0.0, frame_id=1)
    assert context.env_score == pytest.approx(_expected_neutral_e())


def test_assess_degrades_when_frame_content_contradicts_declared_shape() -> None:
    """声明 3 通道、实际像素只有 2 个分量 —— 属于「过了规格检查但内容不符」，
    必须降级而不是让整条链路崩溃（协议禁止 ``assess`` 抛异常）。"""
    pixels = [[[int(value)] * 2 for value in row] for row in _uniform(128.0)]
    frame = FakeFrame(pixels, channels=3)
    context = RuleBasedEnvAgent().assess(frame, ts=0.0, frame_id=1)
    assert context.env_score == pytest.approx(_expected_neutral_e())


def test_assess_degrades_when_pixel_access_raises() -> None:
    frame = ExplodingFrame(_pixels(_uniform(128.0)))
    context = RuleBasedEnvAgent().assess(frame, ts=0.0, frame_id=1)
    assert context.env_score == pytest.approx(_expected_neutral_e())


def test_assess_propagates_timestamps_on_nominal_path() -> None:
    """同一帧的三路结果必须回填相同的 ``ts`` / ``frame_id``（同帧对齐假设）。"""
    context = RuleBasedEnvAgent().assess(_frame(_noise(7)), ts=0.4, frame_id=4)
    assert (context.ts, context.frame_id) == (0.4, 4)
