"""表 A —— 上游类别 → 统一情感标签的映射（骨架，系数待复核）。

这张表解决什么问题
------------------
``PerceptionResult.prob_dist`` 的键**必须是** :class:`~common.perception_types.EmotionLabel`
的三个情感类（见 :data:`~common.perception_types.EMOTION_LABELS`），且总和为 1。
但两路上游模型产出的都不是情感类：

- 表情模型（FER 系数据集）产出的是 ``angry / disgust / fear / happy / sad / surprise / neutral``；
- 行为模型（骨骼点）产出的是**动作类别**，如 ``hand_on_face`` / ``head_down`` / ``lean_forward``。

所以每一路都需要一张「上游类别 → 情感标签」的**行和为 1 的转换矩阵**：把上游
得分与对应行的系数相乘、再归一化，即得到合法的情感分布。数学上就是一次
``S = Mᵀ·p``，其中 ``M`` 是 ``K×3`` 的随机矩阵（非负、行和为 1）。

为什么放在 ``common/`` 而不是各自模块里
--------------------------------------
它是**跨模块契约**：两路智能体都要用它「翻译」自家输出，而融合层与
``tests/test_contract.py`` 依赖「翻译结果一定合法」这条前提。放在共享层，三方的测试
才能调用同一张表；系数一改，哪一路的分布变了立刻可见。

改动流程
--------
- **只改系数**（某行某个数字）：轻量，一名 review 即可 —— 但**必须保持行和 = 1**，
  :func:`validate_tables` 与 ``tests/test_label_mapping.py`` 会挡住违规改动。
- **增删上游类别或目标标签**：属**接口变更**，需三方确认（表情侧影响 B，行为侧影响 A，
  融合与展示口径影响 C）。

⚠️ 骨架说明（重要）
------------------
下面两张表的系数是**占位值**：按常识填入、结构完整、行和均为 1，但**没有经过任何数据或
教学法论证**。落地前必须由各自的模型负责人结合 ``docs/annotation_spec/`` 的标注规范复核，
并补一份「为什么这么映射」的论证（放 ``docs/annotation_spec/``）。

在复核完成之前，这里的分布**只能用于跑通流程与单测，不得用于任何精度结论**。

``UNKNOWN`` 不进分布
-------------------
``UNKNOWN`` 是「本帧未形成判定」的哨兵，不是情感类别。整路弃权时请把
``PerceptionResult.label`` 置为 ``UNKNOWN``，而不是在分布里塞一个未知项。
"""

from __future__ import annotations

from collections.abc import Mapping

from common.perception_types import EMOTION_LABELS, EmotionLabel, PerceptionResult

__all__ = [
    "BEHAVIOR_SOURCE_LABELS",
    "BEHAVIOR_TO_EMOTION",
    "EXPRESSION_SOURCE_LABELS",
    "EXPRESSION_TO_EMOTION",
    "TABLES",
    "dominant",
    "table_row_sums",
    "to_prob_dist",
    "to_result",
    "validate_tables",
]

#: 映射目标：三个情感类。``UNKNOWN`` 刻意不在其中（它是哨兵，不是情感）。
_TARGETS: tuple[EmotionLabel, ...] = EMOTION_LABELS

#: 行和允许的偏差。取得比 ``perception_types`` 的归一化容差（1e-3）更严，
#: 免得「勉强合法」的系数被写进来。
_ROW_SUM_TOL = 1e-6


def _row(focused: float, confused: float, distracted: float) -> dict[EmotionLabel, float]:
    """按固定顺序构造一行系数。

    固定成三参数是为了让**漏填/多填**在语法层面就不可能发生 —— 直接写 dict 字面量时，
    少一个键只会让行和变成 0.8，而 :func:`validate_tables` 要到跑测试才发现。
    """
    return {
        EmotionLabel.FOCUSED: focused,
        EmotionLabel.CONFUSED: confused,
        EmotionLabel.DISTRACTED: distracted,
    }


# ---------------------------------------------------------------------------
# 表情侧：FER 系数据集的 7 类情绪
# ---------------------------------------------------------------------------

#: 上游表情模型的输出类别（顺序无关，仅用于文档与完整性校验）。
EXPRESSION_SOURCE_LABELS: tuple[str, ...] = (
    "angry",
    "disgust",
    "fear",
    "happy",
    "sad",
    "surprise",
    "neutral",
)

#: ⚠️ 占位系数 —— 待 B 结合所选的 FER 数据集与标注规范复核。
EXPRESSION_TO_EMOTION: dict[str, dict[EmotionLabel, float]] = {
    "happy": _row(0.80, 0.10, 0.10),
    "neutral": _row(0.60, 0.20, 0.20),
    "surprise": _row(0.30, 0.60, 0.10),
    "sad": _row(0.10, 0.30, 0.60),
    "angry": _row(0.05, 0.40, 0.55),
    "fear": _row(0.05, 0.55, 0.40),
    "disgust": _row(0.05, 0.35, 0.60),
}


# ---------------------------------------------------------------------------
# 行为侧：由骨骼点序列判定的动作类别
# ---------------------------------------------------------------------------

#: 上游行为模型的动作类别。**这张清单本身就是待定的设计决策** ——
#: 类别粒度（是否区分左右手、是否要 ``writing``）直接决定标注规范与数据量。
BEHAVIOR_SOURCE_LABELS: tuple[str, ...] = (
    "upright",
    "lean_forward",
    "lean_back",
    "hand_on_face",
    "head_down",
    "head_turn",
    "out_of_frame",
)

#: ⚠️ 占位系数 —— 待 A 结合 ``docs/annotation_spec/`` 的标注规范复核。
BEHAVIOR_TO_EMOTION: dict[str, dict[EmotionLabel, float]] = {
    "upright": _row(0.85, 0.10, 0.05),
    "lean_forward": _row(0.35, 0.55, 0.10),
    "lean_back": _row(0.15, 0.15, 0.70),
    "hand_on_face": _row(0.15, 0.55, 0.30),
    "head_down": _row(0.05, 0.30, 0.65),
    "head_turn": _row(0.05, 0.15, 0.80),
    # 人不在画面里：环境层会同时把 E 打到很低，这里给「分心」是保守取值。
    "out_of_frame": _row(0.00, 0.00, 1.00),
}

#: 全部映射表，供 :func:`validate_tables` 与测试批量遍历。
TABLES: dict[str, Mapping[str, Mapping[EmotionLabel, float]]] = {
    "expression": EXPRESSION_TO_EMOTION,
    "behavior": BEHAVIOR_TO_EMOTION,
}


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------


def table_row_sums(
    table: Mapping[str, Mapping[EmotionLabel, float]],
) -> dict[str, float]:
    """返回每一行的行和，便于定位「差一点点」的系数。"""
    return {source: float(sum(row.values())) for source, row in table.items()}


def validate_tables(
    tables: Mapping[str, Mapping[str, Mapping[EmotionLabel, float]]] | None = None,
) -> None:
    """校验所有映射表：键集、取值范围、行和。

    ``tables`` 可注入，便于测试构造非法表来验证校验本身有效。

    Raises:
        ValueError: 任一行的键集不等于 :data:`_TARGETS`、系数不在 [0, 1]、
            或行和偏离 1 超过 ``_ROW_SUM_TOL``。
    """
    for name, table in (TABLES if tables is None else tables).items():
        if not table:
            raise ValueError(f"label table {name!r} must not be empty")
        for source, row in table.items():
            if set(row) != set(_TARGETS):
                missing = sorted(label.value for label in set(_TARGETS) - set(row))
                extra = sorted(str(key) for key in set(row) - set(_TARGETS))
                raise ValueError(
                    f"{name}.{source}: row keys must be exactly the emotion labels; "
                    f"missing={missing}, unexpected={extra}"
                )
            for label, value in row.items():
                if not 0.0 <= float(value) <= 1.0:
                    raise ValueError(
                        f"{name}.{source}[{label.value}] must be in [0, 1], got {value!r}"
                    )
            total = float(sum(row.values()))
            if abs(total - 1.0) > _ROW_SUM_TOL:
                raise ValueError(f"{name}.{source}: coefficients must sum to 1, got {total!r}")


# ---------------------------------------------------------------------------
# 映射与装配
# ---------------------------------------------------------------------------


def _uniform() -> dict[EmotionLabel, float]:
    """无信息时的均匀分布（三项各 1/3）。"""
    share = 1.0 / len(_TARGETS)
    return {label: share for label in _TARGETS}


def to_prob_dist(
    scores: Mapping[str, float],
    table: Mapping[str, Mapping[EmotionLabel, float]],
    *,
    fallback: Mapping[EmotionLabel, float] | None = None,
) -> dict[EmotionLabel, float]:
    """上游得分 × 映射表 → **归一化**的合法情感分布。

    与 ``perception_types`` 的契约一致：键为三个情感类、取值 ∈ [0, 1]、总和为 1。

    行为约定（都在测试里钉住）：

    - **只用表中的键**：上游给出表里没有的类别会被**忽略**（不是抛错）——
      ``infer()`` 禁止抛异常，模型换标签集时不能把整路打挂。
    - **负值与 0 视为无贡献**：上游若是 logits 未过 softmax，负数不该反向投票。
    - **尺度无关**：入参无需自身归一化，函数内部按总和归一 —— 因此既接受
      概率也接受 logits 归一化后的相对大小。
    - **总贡献为 0 时**返回 ``fallback``（未提供则返回均匀分布）。均匀分布表示
      「什么都没测出来」，而不是「三个类各占三分之一」，融合层会按低置信度处理。

    Args:
        scores: 上游类别 → 得分。
        table: 该类别的映射表（``EXPRESSION_TO_EMOTION`` 或 ``BEHAVIOR_TO_EMOTION``）。
        fallback: 总贡献为 0 时使用的分布；不传则用均匀分布。

    Returns:
        三个情感类的归一化分布（全新的 dict，调用方可安全修改）。
    """
    raw = dict.fromkeys(_TARGETS, 0.0)
    for source, weight in scores.items():
        row = table.get(source)
        if row is None:
            continue
        value = float(weight)
        if value <= 0.0:
            continue
        for label in _TARGETS:
            raw[label] += value * float(row.get(label, 0.0))

    total = float(sum(raw.values()))
    if total <= 0.0:
        if fallback is None:
            return _uniform()
        fallback_total = float(sum(float(v) for v in fallback.values()))
        if fallback_total <= 0.0:
            return _uniform()
        return {label: float(fallback.get(label, 0.0)) / fallback_total for label in _TARGETS}

    return {label: raw[label] / total for label in _TARGETS}


def dominant(prob_dist: Mapping[EmotionLabel, float]) -> tuple[EmotionLabel, float]:
    """取分布的最大项。

    并列时按 :data:`EMOTION_LABELS` 的规范顺序裁决 —— 与融合层「数值完全相等时用
    语义无关顺序打破并列」的做法一致，避免结果随 dict 遍历顺序漂移。
    """
    best_label = _TARGETS[0]
    best_value = float(prob_dist.get(best_label, 0.0))
    for label in _TARGETS[1:]:
        value = float(prob_dist.get(label, 0.0))
        if value > best_value:
            best_label, best_value = label, value
    return best_label, best_value


def to_result(
    agent_id: str,
    scores: Mapping[str, float],
    table: Mapping[str, Mapping[EmotionLabel, float]],
    *,
    confidence: float | None = None,
    ts: float = 0.0,
    frame_id: int = 0,
) -> PerceptionResult:
    """一步装配出合法的 :class:`PerceptionResult`。

    存在的意义是**把两类易错点焊死**：

    - ``prob`` 必须等于 ``prob_dist[label]``（契约强制，手写极易写错）；
    - ``label`` 必须是自身 ``prob_dist`` 的 argmax（mock/样例自洽要求）。

    ``confidence`` 默认取分布的最大值（即「本路对当选标签的概率」）。若模型另有一套
    置信度（如人脸检测分数、序列完整度），显式传入以覆盖它 —— 但注意
    ``confidence`` 与 ``prob`` 不是同一个量，不要为了「好看」而抬高于分布。
    """
    prob_dist = to_prob_dist(scores, table)
    label, prob = dominant(prob_dist)
    return PerceptionResult(
        label=label,
        prob=prob,
        confidence=prob if confidence is None else float(confidence),
        agent_id=agent_id,
        ts=ts,
        frame_id=frame_id,
        prob_dist=prob_dist,
    )
