"""环境智能体（C 主责）—— 规则版环境可信度评估。

它回答的问题
------------
不是「这张照片拍得好不好」，而是：

    **「在这帧画面上，表情通道的判断还值不值得相信？」**

这个区别决定了本模块的所有取舍 —— 环境因子 $E$ 是**融合层的权重调节量**
（算法文档 §3.2），它的量纲与含义都由那个用途定义，而不是由图像质量评价体系定义。

为什么先做规则版
----------------
``docs/algorithm.md`` §2.3 与任务计划都明确写了「初期可用亮度/清晰度规则替代」，
``docs/datasets.md`` §5.4 给了三条理由（$E$ 是工程判据不是物理量、规则可解释、
学习法必须打过基线才上线）。本模块因此把三件事分开：

1. **采样与度量**（纯函数，零依赖）—— 无论未来换成 TinyCNN 还是别的模型，
   输入都还是这 32×32 灰度网格；
2. **合成**（:func:`evaluate_luma`）—— 把度量压成 $E$；
3. **协议适配**（:class:`RuleBasedEnvAgent`）—— 只负责契约、降级与时间戳。

未来换 TinyCNN 时只需替换第 2 步，第 1、3 步与测试都能留用。

⚠️ 零第三方依赖是**硬约束**，不是偏好
--------------------------------------
CI 只安装 ``requirements-dev.txt``（无 numpy / opencv），而覆盖率 ``source``
含 ``agents`` —— 所以本模块**只能用标准库**，对帧的访问一律走鸭子类型。
这条约束也顺带保证了它能在任何机器上跑。

``blur`` 这个字段名要按「高频细节的可用量」理解
----------------------------------------------
契约（``EnvContext.blur``）里这个字段叫 ``blur``，但实现测的是
**绝对拉普拉斯方差**，即画面里剩多少高频细节。两者**不等价**：

- 真模糊（失焦、运动模糊）→ 高频被抹掉 → 值升高；
- 本来就没细节的画面（对着白墙、镜头被挡）→ 高频也接近零 → 值同样升高。

对本系统来说**这是有意的**：这两种情形都会让表情通道不可信，而 $E$ 的职责
正是表达「表情通道可不可信」。故不额外区分。代价是它**不能**当通用图像质量
指标用 —— 标定数据见 :data:`_LAP_VAR_REF` 与 ``docs/reports/``。

噪声会抬高拉普拉斯方差
----------------------
传感器噪声本身就是高频，所以**暗光高噪声的画面会被算成「不模糊」**。
单看 ``blur`` 会误判 —— 但 $E$ 是**乘性合成**（亮度因子 × 清晰度因子），
亮度因子会把过暗的画面直接压下去，因此最终 $E$ 仍然正确。
这也解释了为什么不能把 ``blur`` 单独拿去用。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from common.agent_base import EnvAgent
from common.config import DEFAULT_E0, get
from common.input_spec import check_frame
from common.perception_types import EnvContext

__all__ = [
    "MIN_GRID_SIDE",
    "SAMPLE_COLS",
    "SAMPLE_ROWS",
    "EnvMetrics",
    "RuleBasedEnvAgent",
    "evaluate_luma",
    "laplacian_variance",
    "sample_luma",
]

#: 降采样网格尺寸。**刻意取 32×32** —— 与规划中 TinyCNN 分类头的输入尺寸一致，
#: 这样将来把「合成」那一步换成模型时，采样与度量两层可以原样保留。
SAMPLE_COLS = 32
SAMPLE_ROWS = 32

#: 网格短边下限。拉普拉斯需要 3×3 邻域、遮挡分块需要 :data:`_OCC_BLOCK` 的
#: 整数倍，再小就做不出有意义的判定 —— 此时返回中性值而不是硬算一个假数字。
MIN_GRID_SIDE = 8

#: 「细节充足」的参考拉普拉斯方差（32×32 网格、0–255 灰度上的绝对值）。
#:
#: **标定方法**（可复现，命令 `python -m tests.stress.calibration`，
#: 结果见 ``docs/reports/`` 的压测与标定报告）：取确定性合成噪声作「高频充足」的
#: 基准图，**在图像域**连续施加 3×3 盒式模糊模拟失焦（失焦发生在降采样之前，
#: 在网格上做会低估衰减），再降采样并记录拉普拉斯方差：
#:
#: ==================  ==================  ============
#: 模糊遍数             拉普拉斯方差          blur
#: ==================  ==================  ============
#: 0（原始噪声）                100166          0.002
#: 1                              9109          0.021
#: 2                              1237          0.139
#: 3                               237          0.457
#: 5                                32          0.863
#: 8                                 8          0.962
#: ==================  ==================  ============
#:
#: 取 300 的含义是：**容忍线（``blur = blur_tolerance = 0.4``）落在「三遍盒式模糊」
#: 附近**（该遍实测 237，刚刚越过参考值 —— 见上表 blur 在 2 遍与 3 遍之间跨过 0.4）。
#: 这正是「肉眼已能看出糊」的位置；再往后（5 遍起）$E$ 已归零，画面确定不可用。
#:
#: ⚠️ 上表是**合成噪声**上的量尺，用于说明常数的量级与单调性，**不代表真实摄像头
#: 的绝对水平**（不同传感器、镜头、光照下同一物体的拉普拉斯方差可差一个数量级）。
#: 因此这是**经验值**，且是**绝对**阈值（不随画面内容自适应）—— 换摄像头、
#: 换光照条件后必须用同一脚本重新标定。用规则法就是为了这一步能被快速做掉；
#: 换成学习模型后这个常数才会消失。
#:
#: 📌 标定归属：``blur_tolerance`` 的取值由 C 维护，但**锚点是否合理**（「三遍糊」
#: 是否真的对应「还能用」）需要与 A（全系统调参负责人）在目标机上共同确认。
_LAP_VAR_REF = 300.0

#: 拉普拉斯方差恰为 :data:`_LAP_VAR_REF` 时的 ``blur`` 取值。
#:
#: 取默认容忍线 0.4 作为锚点，是为了让 ``blur`` 这个**观测量**有一个与配置
#: 无关的固定标度（见 :data:`_BLUR_SHAPE`）。否则 ``blur`` 的数值会随
#: ``blur_tolerance`` 一起漂移，那样「同一条曲线」在不同配置下报出不同的
#: 模糊度，调试时无法比较。
_BLUR_AT_REFERENCE = 0.4

#: 双曲映射 ``blur = _BLUR_SHAPE / (_BLUR_SHAPE + x)`` 的形状参数，
#: 由 ``x = 1 ⇔ blur = _BLUR_AT_REFERENCE`` 解出：$a = t / (1 - t)$。
_BLUR_SHAPE = _BLUR_AT_REFERENCE / (1.0 - _BLUR_AT_REFERENCE)

#: 清晰度惩罚的**归零倍率**：``blur`` 达到容忍线的 2 倍时，清晰度因子归零。
#:
#: 为什么不是「一到容忍线就归零」：容忍线的语义是「临界」，临界点应当只剩
#: 一半可信度而不是全无。线性从 1 衰减到 0、在 $2\\times$ 容忍线处归零，
#: 既保持单调、又让「比临界更糟」有明确的尽头。
_CLARITY_ZERO_RATIO = 2.0

#: 遮挡判定的块内亮度标准差上限（0–255）：低于它视为「平坦区域」。
_OCC_FLAT_STD = 3.0
#: 块内亮度方差上限（避免在循环里开方，故直接存平方值）。
_OCC_FLAT_VAR = _OCC_FLAT_STD * _OCC_FLAT_STD
#: 平坦块还要**同时**满足亮度极端（过暗或过曝）才算被遮挡，见 :func:`_blocked_ratio`。
_OCC_DARK_LEVEL = 0.15
_OCC_BRIGHT_LEVEL = 0.92
#: 遮挡判定的块边长（以采样点为单位的正方形边长）。
_OCC_BLOCK = 4

#: OpenCV 的 BGR → 灰度权重（ITU-R BT.601）。
_LUMA_B, _LUMA_G, _LUMA_R = 0.114, 0.587, 0.299


def _clamp01(value: float) -> float:
    """把取值夹到 [0, 1] 闭区间。"""
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


def sample_luma(
    frame: Any, *, cols: int = SAMPLE_COLS, rows: int = SAMPLE_ROWS
) -> list[list[float]]:
    """把帧降采样成 ``rows × cols`` 的亮度网格（0–255）。

    每个网格点取对应**块中心**的像素（最近邻）—— 不取块内平均，因为平均本身
    就是一次低通滤波，会把要测的高频细节抹掉一部分，让模糊判定自相矛盾。

    只用鸭子类型访问（``.shape`` 与 ``frame[y][x][c]``），故 numpy 数组与
    「嵌套列表 + shape/dtype 外壳」的测试替身都能喂进来。

    Args:
        frame: 已通过 :func:`common.input_spec.check_frame` 的帧。
        cols: 采样列数。
        rows: 采样行数。

    Returns:
        亮度网格，取值 0–255。

    Raises:
        AttributeError / IndexError / TypeError: 帧的实际内容与声明的
            ``shape`` / ``dtype`` 不符时由属性访问自然抛出 —— **本函数不做降级**，
            降级是调用方 :meth:`RuleBasedEnvAgent.assess` 的职责
            （协议禁止智能体抛异常）。
    """
    height = int(frame.shape[0])
    width = int(frame.shape[1])
    grid: list[list[float]] = []
    for row_index in range(rows):
        # 块中心的整数近似：(2i + 1) * h / (2 * rows)
        y = min(height - 1, (2 * row_index + 1) * height // (2 * rows))
        source_row = frame[y]
        line: list[float] = []
        for col_index in range(cols):
            x = min(width - 1, (2 * col_index + 1) * width // (2 * cols))
            pixel = source_row[x]
            line.append(
                _LUMA_B * float(pixel[0]) + _LUMA_G * float(pixel[1]) + _LUMA_R * float(pixel[2])
            )
        grid.append(line)
    return grid


def laplacian_variance(grid: Sequence[Sequence[float]]) -> float:
    """返回 4-邻域拉普拉斯算子的方差。

    核为 ``[0, 1, 0; 1, -4, 1; 0, 1, 0]``，只统计内部像素 —— **边缘不做填充**：
    补零会在边界凭空造出梯度，反而让判定偏向「清晰」。

    网格短边小于 3 时返回 0.0（=「没有细节」）。本函数不返回中性值，
    以免把「测不出来」和「确实没有细节」混成同一个数 —— 后者由
    :func:`evaluate_luma` 解释。
    """
    rows = len(grid)
    cols = len(grid[0]) if rows else 0
    if rows < 3 or cols < 3:
        return 0.0

    values: list[float] = []
    for y in range(1, rows - 1):
        above, middle, below = grid[y - 1], grid[y], grid[y + 1]
        for x in range(1, cols - 1):
            values.append(above[x] + below[x] + middle[x - 1] + middle[x + 1] - 4.0 * middle[x])

    # rows/cols 均 ≥ 3 时内部像素至少有一个，故 values 必非空。
    count = len(values)
    mean = sum(values) / count
    return sum((value - mean) ** 2 for value in values) / count


def _blocked_ratio(grid: Sequence[Sequence[float]]) -> float:
    """估算「被贴近镜头的物体占据」的画面比例（0–1）。

    这是一个**代理指标**，不是真正的遮挡检测 —— 环境智能体只拿到全帧，
    没有脸框，因此无法直接判定「人脸是否可见」。可用的线索是：贴近镜头的
    物体（手掌、书本、身体）在画面上呈现为**大片平坦且亮度极端**的区域
    （背光成剪影 → 过暗；被灯直照 → 过曝）。

    判据（两个条件**同时**满足）：

    1. 块内亮度标准差 < :data:`_OCC_FLAT_STD`（平坦、没有纹理）；
    2. 块内平均亮度 < :data:`_OCC_DARK_LEVEL` 或 > :data:`_OCC_BRIGHT_LEVEL`
       （亮度极端）。

    ⚠️ **第 2 条是刻意加的，不是保守**：如果只按「平坦」判定，那么对着白墙、
    空教室的画面都会被算成遮挡，融合层就会莫名压制表情通道；而这类画面本来
    就不该靠遮挡机制处理 —— 它们没有细节 → ``blur`` 升高 → 清晰度因子已经把
    $E$ 压下去了。遮挡机制要管的恰恰是**「$E$ 很高、但目标看不见」**这一种，
    也就是 $E$ 单独表达不了的情形（算法文档 §3.4）。
    实测：中灰（128）平坦方块**不计**遮挡，而同样大小的过暗（10）/过曝（250）
    方块都计入 —— 这正是设计意图。

    **已知局限**：分辨不出「手掌挡住镜头」与「镜头正对一块深色桌面」；
    也无法判断遮挡位置是否落在人脸区域。等表情智能体具备人脸定位能力后，
    遮挡应改为由脸框推导（那是更强的信号），本函数届时应退役。
    """
    rows = len(grid)
    cols = len(grid[0]) if rows else 0
    if rows < _OCC_BLOCK or cols < _OCC_BLOCK:
        return 0.0

    total = 0
    blocked = 0
    for top in range(0, rows - _OCC_BLOCK + 1, _OCC_BLOCK):
        for left in range(0, cols - _OCC_BLOCK + 1, _OCC_BLOCK):
            block = [
                grid[y][x]
                for y in range(top, top + _OCC_BLOCK)
                for x in range(left, left + _OCC_BLOCK)
            ]
            size = len(block)
            mean = sum(block) / size
            variance = sum((value - mean) ** 2 for value in block) / size
            total += 1
            if variance > _OCC_FLAT_VAR:
                continue
            level = mean / 255.0
            if level < _OCC_DARK_LEVEL or level > _OCC_BRIGHT_LEVEL:
                blocked += 1

    # rows/cols 均 ≥ _OCC_BLOCK 时至少有一个完整块，故 total ≥ 1。
    return blocked / total


def _neutral_metrics() -> EnvMetrics:
    """网格无法判定时使用的中性度量。"""
    return EnvMetrics(brightness=0.5, blur=0.5, occlusion=0.0, env_score=float(DEFAULT_E0))


@dataclass(frozen=True)
class EnvMetrics:
    """环境度量的纯数据结果（不含时间戳/帧号）。

    与 :class:`~common.perception_types.EnvContext` 分开，是为了让
    :func:`evaluate_luma` 能脱离「帧」与「协议」单独测试 —— 它只依赖一个
    亮度网格。两者字段一一对应，由 :meth:`RuleBasedEnvAgent.assess` 负责搬运。
    """

    brightness: float
    blur: float
    occlusion: float
    env_score: float


def evaluate_luma(
    grid: Sequence[Sequence[float]],
    *,
    blur_tolerance: float = 0.4,
    brightness_min: float = 0.25,
    brightness_max: float = 0.95,
    occlusion_weight: float = 0.0,
) -> EnvMetrics:
    """亮度网格 → 环境度量与可信度因子 $E$。

    $E$ 的合成方式
    --------------
    先算两个「可信度」因子，再**相乘**：

    .. math::

        E = (1 - \\max(\\text{过暗程度},\\ \\text{过曝程度}))
            \\times \\Big(1 - \\frac{\\text{blur}}{2 \\times \\text{tol}}\\Big)

    取乘性而非加权平均，是因为两者是**独立的失效模式**：画面既糊又暗时，
    任一单项都无法单独代表整体可用性，而乘性合成下**任一项接近 0 就把 $E$
    拉到底**（这也是可靠性工程的常规做法）。加权平均会让「极暗但清晰」拿到
    一半分数，从而错误地把权重留在表情通道。

    清晰度惩罚为什么是 ``blur / (2 × tol)``
    ----------------------------------------
    ``blur_tolerance`` 的语义是**临界线**，所以：

    - ``blur == tol``（正好临界）→ 清晰度因子 = 0.5，即「只剩一半可信度」；
    - ``blur == 2 × tol``（明显超出）→ 归零；
    - ``blur == 0`` → 1。

    这样**提高容忍线会让同一画面得分变好**（方向正确），而若把容忍线直接
    当分母（``blur / tol``），临界点会归零、且调高容忍线反而变严格 —— 方向
    是反的，属于必须在实现里避免的错误。

    遮挡**不参与合成**（``occlusion_weight`` 固定为 0）
    --------------------------------------------------
    这是刻意的，见 ``configs/thresholds.yaml`` 的 ``env_agent.occlusion_weight``
    与算法文档 §3.4：遮挡若在这里折算进 $E$，就会同时经由 §3.2 的权重转移和
    §3.4 的行为增益**被计入两次**，补偿强度失控。因此遮挡只作为观测量上报。

    参数仍然从配置读进来并生效，是为了不让它变成一个「没有读取方的配置项」——
    那样反而会让人误以为改它有用；同时该分支在测试里被显式覆盖。

    Args:
        grid: 亮度网格（0–255），见 :func:`sample_luma`。
        blur_tolerance: 模糊容忍线（``env_agent.blur_tolerance``）。
        brightness_min: 过暗阈值（``env_agent.brightness_min``）。
        brightness_max: 过曝阈值（``env_agent.brightness_max``）。
        occlusion_weight: 遮挡在 $E$ 中的权重（``env_agent.occlusion_weight``，须为 0）。

    Returns:
        环境度量。网格为空或短边小于 :data:`MIN_GRID_SIDE` 时返回**中性值**
        （$E = E_0$、brightness = blur = 0.5、occlusion = 0），与
        :meth:`common.agent_base.EnvAgent._neutral` 同源。
    """
    rows = len(grid)
    cols = len(grid[0]) if rows else 0
    if rows < MIN_GRID_SIDE or cols < MIN_GRID_SIDE:
        return _neutral_metrics()

    brightness = _clamp01(sum(sum(row) for row in grid) / (rows * cols) / 255.0)

    # 亮度因子：区间内为 1，越界线性衰减到 0（brightness 触到 0 或 1 时归零）。
    if brightness < brightness_min:
        dark_excess = _clamp01((brightness_min - brightness) / max(brightness_min, 1e-9))
    else:
        dark_excess = 0.0
    if brightness > brightness_max:
        span = max(1.0 - brightness_max, 1e-9)
        bright_excess = _clamp01((brightness - brightness_max) / span)
    else:
        bright_excess = 0.0
    brightness_factor = 1.0 - max(dark_excess, bright_excess)

    # 清晰度因子：blur 是与配置无关的观测量，容忍线只作用于惩罚比例。
    excess = laplacian_variance(grid) / _LAP_VAR_REF
    blur = _BLUR_SHAPE / (_BLUR_SHAPE + excess)
    span = _CLARITY_ZERO_RATIO * blur_tolerance
    clarity_factor = 0.0 if span <= 0.0 else _clamp01(1.0 - blur / span)

    occlusion = _blocked_ratio(grid)

    env_score = brightness_factor * clarity_factor
    if occlusion_weight > 0.0:
        env_score *= 1.0 - _clamp01(occlusion_weight) * occlusion

    return EnvMetrics(
        brightness=brightness,
        blur=blur,
        occlusion=occlusion,
        env_score=_clamp01(env_score),
    )


class RuleBasedEnvAgent(EnvAgent):
    """规则版环境智能体。

    用法::

        agent = RuleBasedEnvAgent()
        agent.warmup()
        context = agent.assess(frame, ts=1.0, frame_id=3)   # -> EnvContext

    配置在**构造时**读取一次（``configs/thresholds.yaml`` 的 ``env_agent`` 段），
    也允许显式传参覆盖 —— 后者是给标定与测试用的：标定「多暗算过暗」时不该
    每次都去改配置文件（``configs/`` 的 key 结构变化属于接口变更，要走三方评审；
    只改数值虽然轻量，但频繁改动会污染 diff）。

    线程安全：协议已明确同一实例不会被并发调用（调度归 ``pipeline/``），
    故本实现不加锁。
    """

    def __init__(
        self,
        *,
        blur_tolerance: float | None = None,
        brightness_min: float | None = None,
        brightness_max: float | None = None,
        occlusion_weight: float | None = None,
    ) -> None:
        self.blur_tolerance = (
            float(get("env_agent", "blur_tolerance", default=0.4))
            if blur_tolerance is None
            else float(blur_tolerance)
        )
        self.brightness_min = (
            float(get("env_agent", "brightness_min", default=0.25))
            if brightness_min is None
            else float(brightness_min)
        )
        self.brightness_max = (
            float(get("env_agent", "brightness_max", default=0.95))
            if brightness_max is None
            else float(brightness_max)
        )
        self.occlusion_weight = (
            float(get("env_agent", "occlusion_weight", default=0.0))
            if occlusion_weight is None
            else float(occlusion_weight)
        )

    def warmup(self) -> None:
        """空实现。

        规则法没有需要加载的资源。保留这个钩子是因为**集成方会调用它**
        （``pipeline/`` 启动时对三路一视同仁），将来替换成 TinyCNN 时在这里
        加载权重即可，集成代码不需要改。
        """

    def assess(self, frame: Any, ts: float, frame_id: int) -> EnvContext:
        """评估单帧环境质量。

        降级顺序（任一环节出问题都返回中性值，**绝不抛异常**）：

        1. :func:`common.input_spec.check_frame` 判定帧不符合规格（含 ``None``
           无帧哨兵）→ 中性；
        2. 采样或度量内部异常（声明的 ``shape`` / ``dtype`` 与实际内容不符、
           自定义帧对象行为异常）→ 中性。

        中性值的取值理由见 :meth:`common.agent_base.EnvAgent._neutral`：
        $E$ 必须取 ``weights.e0`` 而不是 0.5，否则等于在评估失败时偷偷假设
        环境很差。
        """
        if check_frame(frame):
            return self._neutral(ts, frame_id)

        try:
            metrics = evaluate_luma(
                sample_luma(frame),
                blur_tolerance=self.blur_tolerance,
                brightness_min=self.brightness_min,
                brightness_max=self.brightness_max,
                occlusion_weight=self.occlusion_weight,
            )
        except Exception:
            # 协议要求 assess() 永不抛异常。这里捕获的是「帧通过了规格检查、
            # 但实际内容与声明不符」这一类问题（例如 dtype 声称 uint8，而像素
            # 序列只有两个分量），属于必须降级而不是让整条链路崩溃的情况。
            return self._neutral(ts, frame_id)

        return EnvContext(
            brightness=metrics.brightness,
            blur=metrics.blur,
            env_score=metrics.env_score,
            occlusion=metrics.occlusion,
            ts=ts,
            frame_id=frame_id,
        )
