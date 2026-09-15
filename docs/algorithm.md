# 灵犀学伴 —— 算法与数学模型技术文档

本文档描述“灵犀学伴”系统中使用的核心算法与数学模型，与仓库代码实现保持同步。

> **同步声明**：本文档第 3 章的权重函数与协商规则已于实现中落地，代码位置见各节
> “实现”标注。文档与代码不一致时以代码为准，并及时回改本文档。
>
> **修订记录**：2026-09-15 —— 第 3 章按运行时核验结果重写：补全 §3.1 的数据来源
> （`prob_dist` 完整分布）与降级路径、新增 §3.3 的 `conflict` 级别、修正 §3.4 中
> 关于“归一化会抵消惩罚”的错误论证、明确遮挡只允许计入一次，并补记 §3.2 的环境
> 缺失先验与 §7 的假设与局限。

## 1. 系统概述

灵犀学伴是一个面向边缘设备的轻量化多智能体情感协同感知系统，采用三阶段处理流程：

```text
原始图像 → 多源感知 → 空间融合 → 时间平滑 → 稳定输出
```

对应的代码层次：

| 阶段 | 代码位置 |
|------|---------|
| 多源感知 | `agents/expression/`、`agents/behavior/`、`agents/env/` |
| 空间融合 | `fusion/weights.py`、`fusion/fusion_engine.py` |
| 时间平滑 | `fusion/temporal_smoother.py` |
| 稳定输出 | `app/`（消费 `FinalState`） |

## 2. 多源并行感知阶段

三个智能体的标识符在 `common/perception_types.py` 中全局唯一约定，禁止使用别名：

| 常量 | 取值 | 含义 |
|------|------|------|
| `AGENT_EXPRESSION` | `"expression"` | 表情智能体 |
| `AGENT_BEHAVIOR` | `"behavior"` | 行为智能体 |
| `AGENT_ENV` | `"env"` | 环境智能体 |

> 历史文档中出现的 `face` / `pose` 为旧写法，一律对应 `expression` / `behavior`。
> 代码中不得再出现旧名称，由 `tests/test_contract.py` 自动守卫。

### 2.1 表情智能体（精细感知）

- **模型架构**：MobileNetV3 轻量化卷积神经网络
- **输入**：通过 Haar 级联分类器定位的人脸 ROI 区域
- **输出**：统一标签体系下的概率分布 $P_{\text{expression}}$ 与置信度
- **实现**：`agents/expression/`，`agent_id = AGENT_EXPRESSION`

### 2.2 行为智能体（姿态感知）

- **模型架构**：MediaPipe Pose + LSTM（长短期记忆网络）
- **处理流程**：视频帧 → 骨骼关键点坐标 → LSTM 时序建模
- **输出**：行为概率分布 $P_{\text{behavior}}$（识别“托腮”、“低头”、“前倾”等）
- **实现**：`agents/behavior/`，`agent_id = AGENT_BEHAVIOR`

### 2.3 环境智能体（效度评估）

- **模型架构**：TinyCNN（自定义极轻量模型），初期可用亮度/清晰度规则替代
- **输入**：全帧画面降采样
- **输出**：环境可信度因子 $E \in [0, 1]$，以及亮度、模糊度、遮挡程度
- **实现**：`agents/env/`，输出 `EnvContext`

$E$ 越高表示环境越可信（光照充足、画面清晰、无明显遮挡），表情通道的判定越可靠。

## 3. 空间协同决策阶段（动态加权融合）

### 3.1 数学模型

系统采用**动态加权融合模型**计算瞬时情感置信度：

$$S(\ell) = \sum_{i} w_i(E) \cdot P_i(\ell)$$

其中：
- $S(\ell)$: 系统对标签 $\ell$ 的综合加权得分
- $i$: 参与融合的感知智能体索引（`expression` 与 `behavior`；`env` 提供 $E$ 但不参与情感投票）
- $w_i(E)$: 受环境可信度因子 $E$ 影响的动态权重函数
- $P_i(\ell)$: 智能体 $i$ 对标签 $\ell$ 的判定概率

#### 数据来源与降级路径

$P_i(\ell)$ 取自每路输出的**完整概率分布** `PerceptionResult.prob_dist`，
其键为三类情感标签（`focused` / `confused` / `distracted`）且总和为 1。
两路分布都归一化、权重之和也为 1 时，$S$ 本身就是一个归一化分布：

$$\sum_{\ell} S(\ell) = \sum_i w_i(E) \cdot \underbrace{\sum_{\ell} P_i(\ell)}_{=1} = \sum_i w_i(E) = 1$$

这正是 §3.3 中 `low_confidence`（绝对阈值）能够成立的前提。

> **降级路径**：若某路只填了标量 `prob`（未填 `prob_dist`），融合层退化为在它的
> top 标签上累加 $w_i \cdot \text{prob}_i$ —— 这与旧的加权多数投票逐位等价，仅用于
> 兼容尚未改造的智能体。此时 $\sum_\ell S(\ell) \le 1$，且第二名候选的票数被整块
> 丢弃，会给出偏自信的结论。**新代码必须填充 `prob_dist`。**

#### 输出规则

取加权得分最高的标签 $\ell^* = \arg\max_{\ell} S(\ell)$，置信度为
$\min\big(1, S(\ell^*)\big)$。但 $\ell^*$ **不是无条件输出**：协商级别
（§3.3）可以否决它 —— 命中 `low_confidence` 或 `conflict` 时统一输出
`EmotionLabel.UNKNOWN`。

得分并列时的裁决顺序按 `common/perception_types.py::EMOTION_LABELS` 固定
（`focused → confused → distracted`），与字典遍历顺序无关，保证可复现。
需强调：该顺序**只在数值完全相等时才被用到**；只要首位与次位的差距小于
`negotiation.conflict_margin`，判定就会落到 `conflict` 而不是按顺序硬选一个，
因此它不会给任何一类标签带来系统性偏置。

**实现**：`fusion/fusion_engine.py::FusionEngine.fuse`

### 3.2 动态权重函数

权重函数设计为环境相关的**连续可导**函数，采用 logistic 形式：

$$w_{\text{expression}}(E) = \frac{1}{1 + \exp\big(-k\,(E - E_0)\big)}$$

$$w_{\text{behavior}}(E) = 1 - w_{\text{expression}}(E)$$

参数取值（可在 `configs/thresholds.yaml` 的 `weights` 段调整）：

| 参数 | 默认值 | 含义 |
|------|-------|------|
| $E_0$ | 0.6 | logistic 中心点：$E = E_0$ 时两路权重相等 |
| $k$ | 8.0 | 斜率：越大过渡越陡，8.0 约对应 $0.35 \sim 0.85$ 的过渡带 |

典型取值：

| $E$ | $w_{\text{expression}}$ | $w_{\text{behavior}}$ |
|-----|------------------------|----------------------|
| 0.90 | 0.917 | 0.083 |
| 0.80 | 0.832 | 0.168 |
| 0.60 | 0.500 | 0.500 |
| 0.40 | 0.168 | 0.832 |
| 0.35 | 0.119 | 0.881 |

> **与旧版本的差异**：早期文档将权重定义为分段常数
> （$E > 0.8$ 取 0.7、$0.4 < E \le 0.8$ 取 0.5、$E \le 0.4$ 取 0.3）。
> 该定义在阈值处发生阶跃，与“权重变化为连续过程而非开关切换”的设计目标自相矛盾。
> 现统一改为 logistic 连续形式；$E=0.8$ 时 $w \approx 0.83$、$E=0.4$ 时 $w \approx 0.17$，
> 覆盖了原 0.7:0.3 ↔ 0.3:0.7 的设计区间且处处连续。

**实现**：`fusion/weights.py::w_face` / `w_behavior`，连续性与单调性由
`tests/fusion/test_fusion.py` 以 0.001 步长扫描断言（相邻步最大跳变 < 0.01）。
**注意**：代码与本文档统一使用 `expression` / `behavior` 命名，不再使用 `face` / `pose`
（上述函数名 `w_face` 为兼容既有调用保留，语义等同 `w_expression`）。

#### 环境未知时的中性先验

融合层拿不到 `EnvContext`（`env=None`）时取 $E = E_0$（默认 0.6），此时
$w_{\text{expression}} = w_{\text{behavior}} = 0.5$，是真正“无信息”的先验。

不可取固定 $E = 0.5$：由于 $0.5 < E_0$，logistic 会把权重压成约
$0.31 : 0.69$，等于在环境未知时**悄悄假设环境很差**，把判断权单方面让给行为
通道。对称输入（两路等概率、结论相反）会因此被错误地判为“行为通道胜出”，
而正确行为是判为无法定论（见 §3.3 的 `conflict`）。

**实现**：`fusion/fusion_engine.py::FusionEngine._neutral_E`

### 3.3 协商级别与冲突识别

这些级别**不是几套独立逻辑**，而是同一套加权计算的几种表现结果。实现上先统一
计算权重与得分，再判定本次命中了哪一级（记录在 `FusionOutput.reason`），
避免“一级直出”与“二级加权”两套代码给出不一致答案。

| 级别 | 条件 | 策略 | `reason` 取值 |
|------|------|------|--------------|
| 一级 | 高可信一致：$E > 0.8$ 且无遮挡且两路标签一致 | 直接输出共识结果 | `consensus` |
| 二级 | 环境驱动降权：环境较差、存在遮挡，或两路判断不一致需消解 | 动态调整权重后加权 | `reweight` |
| 三级 | 低置信：加权最高得分 $< 0.4$ | 输出 `UNKNOWN`，避免误判 | `low_confidence` |
| 冲突 | 无法定论：首位与次位加权得分之差 $< 0.05$ | 输出 `UNKNOWN`，拒绝二选一 | `conflict` |
| （兜底） | 无任何有效输入 | 直接返回 `UNKNOWN` | `empty` |

**判定优先级**：`low_confidence` > `conflict` > `consensus` > `reweight` ——
前两者属于“不下结论”，一旦命中，等级再高也不强判，统一输出
`EmotionLabel.UNKNOWN`。

#### 为什么需要 `conflict`

仅靠“取最高分”无法区分两种本质不同的情形：

- **分歧已被权重消解**：两路结论相反，但一路明显更可信（例如弱光下行为权重
  0.92、表情权重 0.08），此时按权重裁决是合理的，应记 `reweight`；
- **分歧无法消解**：两路结论相反且加权得分几乎持平，此时任何“选一个”都只是
  把内部遍历顺序伪装成结论。旧实现用 `max(scores, key=(得分, 标签字符串))`，
  在平局时会**系统性地偏向字符串序靠前的 `focused`**，让系统在最该谨慎的时候
  表现得最自信。引入 `conflict` 后，这类输入一律输出 `UNKNOWN`。

阈值 $0.05$ 的含义是“两条候选的差距不足 5 个百分点即视为无区分度”。

阈值可在 `configs/thresholds.yaml` 的 `negotiation` 段调整：
`high_trust_E` (0.8)、`low_confidence` (0.4)、`occlusion_trigger` (0.5)、
`conflict_margin` (0.05)。

**实现**：`fusion/fusion_engine.py::FusionEngine._classify`

### 3.4 遮挡补偿（仿生感官补偿的落地点）

当遮挡程度超过 `occlusion_trigger` (0.5) 时，人脸像素本身已不可信，
无论 $E$ 多高都不应让表情通道主导判断。此时对行为通道施加相对增益：

$$\text{gain} = \frac{1}{\max\big(0.05,\ 1 - \text{excess}\big)}$$

$$\text{excess} = \frac{\text{occlusion} - \text{trigger}}{1 - \text{trigger}}$$

$$w_{\text{behavior}} \leftarrow w_{\text{behavior}} \cdot \text{gain}$$

随后对两路权重重新归一化，使权重之和恒为 1。

#### 增益的等价性与真正的陷阱

设补偿前的权重为 $(a, b)$，$a + b = 1$。把行为通道乘以 $\text{gain}$ 后再归一化：

$$\frac{b_{\text{new}}}{a_{\text{new}}} = \text{gain} \cdot \frac{b}{a}$$

可见结果只取决于两路权重的**比值**被放大了多少倍。于是“把互补通道乘以系数”
与“把本通道乘以该系数的倒数”是**完全等价的两种写法**
（$\text{gain} \leftrightarrow 1/\text{penalty}$），并不存在“先压低再归一化会把
惩罚抵消掉”的现象 —— 该表述曾出现在本文档中，已被运行时实验否定。

真正会出错的只有两件事：

1. **忘记归一化**：权重之和不再是 1，$S$ 的尺度随之漂移，
   `negotiation.low_confidence` 这个绝对阈值失去意义；
2. **两路同乘同一个系数**：比值不变，等于没有补偿。

本实现选择“放大行为通道”这一写法，因为增益方向更直观（遮挡越重 →
行为越被信任）。

#### 遮挡只能计入一次

遮挡**必须只在本节处理**。若环境智能体在合成 $E$ 时也把遮挡折算进去
（见 `configs/thresholds.yaml` 的 `env_agent.occlusion_weight`），遮挡就会同时
经由 §3.2 的权重转移和本节的增益作用两次，补偿强度失控。
因此该配置项固定为 `0.0`，遮挡仅通过 `EnvContext.occlusion` 单独上报，
由融合层统一解释。

实测权重转移效果（$E = 0.9$ 的良好环境下，仅改变遮挡程度）：

| occlusion | $w_{\text{expression}}$ | $w_{\text{behavior}}$ | 说明 |
|-----------|------------------------|----------------------|------|
| 0.00 | 0.917 | 0.083 | 无遮挡，表情主导 |
| 0.60 | 0.898 | 0.102 | 刚过触发线，补偿轻微 |
| 0.85 | 0.768 | 0.232 | 补偿明显 |
| 0.95 | 0.524 | 0.476 | 接近持平 |
| 1.00 | 0.355 | 0.645 | 行为反超，接管判断 |

该表体现了本机制的核心价值：**即使环境可信度 $E$ 很高（光照、清晰度都很好），
只要人脸被遮挡，表情通道也会被强制让位**。这是 $E$ 单独无法表达的信息 ——
$E$ 描述“画面整体质量”，而遮挡描述“目标本身是否可见”，两者必须分别处理。

**实现**：`fusion/fusion_engine.py::FusionEngine._resolve_weights`

### 3.5 通道缺失降级

融合层允许缺少任意一路输入：

- **两路均在线**：按 $w(E)$ 加权融合
- **仅一路在线**：该路权重自动补为 1.0，直接输出其判定
- **两路均缺失**：返回 `UNKNOWN`，`reason = "empty"`

这一机制使三位成员的模块可以真正并行开发——某人的模型尚未交付时，
融合层不会崩溃，也不会阻塞其余两人的进度。

**实现**：`fusion/weights.py::normalized_weights`

## 4. 时间维度平滑阶段

### 4.1 “3中2”时序滑动窗口

为过滤眨眼、转头等瞬时干扰，系统采用时序平滑：

```text
输入: 长度为 3 的滑动窗口内的标签序列
输出:
  - 若某标签在窗口内出现 >= 2 次 → 确认并输出该稳定状态
  - 否则 → 保持上一个稳定状态，并标记 stale = True
```

参数（`configs/thresholds.yaml` 的 `smoothing` 段）：窗口 `window = 3`，
确认票数 `min_votes = 2`。

### 4.2 `stale` 语义与“保持上一状态”

当票数不足时，系统**保持上一个稳定状态**并标记 `FinalState.stale = True`，
而不是输出 `UNKNOWN`。

理由：直接输出 `UNKNOWN` 会导致演示过程中状态频繁闪断（状态在
“确认值 ↔ 不确定”之间来回跳）。保持上一状态并标记 `stale`，
前端可表现为“状态维持中”，显著改善视觉稳定性。

**注意**：`UNKNOWN` 不会被计入投票窗口 —— 它的含义是“本帧未形成判定”，
而不是一种情感状态。将其计入会使窗口被无效票污染。

`stale = True` 时，`FinalState` 的 `timestamp` 与 `frame_id` 仍回填**当前帧**的值，
避免前端误判为数据停滞。

可通过 `hold_on_insufficient: false` 切换回输出 `UNKNOWN` 的行为（不建议）。

**实现**：`fusion/temporal_smoother.py::TemporalSmoother.update`

### 4.3 内存复杂度

$O(1)$ 级常数内存开销，仅需存储窗口内三帧状态。

## 5. 仿生机制

系统模拟生物的“感官补偿”机制：

> 当某一感官（视觉表情）受阻时，大脑会自动增强其他感官（行为姿态）的信号增益。

在算法中体现为两个层面：

1. **环境驱动权重转移**（§3.2）：环境质量下降时，表情权重自动降低，行为权重自动升高；
2. **遮挡强制补偿**（§3.4）：遮挡严重时，不等待 $E$ 的变化，直接对行为通道施加增益。

## 6. 统一标签体系

三个智能体共享 4 类标签（`common/perception_types.py::EmotionLabel`）：

| 枚举值 | 字符串 | 对应说明文档表述 |
|--------|-------|----------------|
| `FOCUSED` | `focused` | 持续专注 |
| `CONFUSED` | `confused` | 瞬时困惑 |
| `DISTRACTED` | `distracted` | 可能分心 |
| `UNKNOWN` | `unknown` | 不确定（置信度过低或输入无效时的安全输出） |

> **二分类映射规则**：教学场景若只需“专注 / 非专注”两类，请在**输出层**
> （`app/` 或最终报告生成处）做映射，**不要在智能体层裁剪标签空间**。
> 智能体层保留 4 类可以给消融实验和后续扩展留出空间；提前降为 2 类会不可逆地
> 丢失信息（例如“困惑”与“分心”的教学含义完全不同，合并后无法还原）。

## 7. 性能指标与假设

| 指标 | 目标值 | 测量位置 |
|------|-------|---------|
| 端到端延迟 | < 200ms | `tests/stress/` 记录 P95 |
| 模型大小 | < 50MB（边缘部署） | `agents/expression/`、`agents/env/` |
| 准确率 | > 85%（理想环境） | 各模块分别测量后汇总 |

> 指标应区分**瞬时推理延迟**与**稳定状态确认延迟**：后者额外包含时序平滑
> 所需的 1~2 帧等待，二者不可混为一谈。

### 7.1 假设与局限

1. **同帧对齐假设**：§3.1 的 $S(\ell)$ 假定参与融合的各路输出属于**同一帧**。
   若某路丢帧或延迟过大，权重就会作用在不同时刻的证据上，融合结果失去意义。
   该假设由采集侧回填统一的 `ts` / `frame_id` 支撑（见
   `common/perception_types.py`），并由 `pipeline/` 的丢帧判据
   （`pipeline.drop_stale_after`）保证 —— 但它是**工程约束**，不是数学模型自身
   的性质。
2. **通道独立性假设**：$w_i(E)$ 只依赖 $E$，不建模两路之间的相关性。当两路因同
   一扰动（如运动模糊）同时退化时，加权求和无法识别“共同误差”，可能给出高于
   实际的置信度。
3. **置信度未经概率校准**：$S(\ell^*)$ 是加权后的**得分**，不是校准后的概率。它
   可比较、可设定绝对阈值，但**不可直接当作准确率**解读（例如 0.8 并不表示
   “有 80% 的概率正确”）。
4. **阈值是经验值**：`low_confidence`、`conflict_margin`、`occlusion_trigger`
   等均按当前标签体系与 mock 场景标定，接入真实数据后需要重新校准。

## 8. 数学符号汇总

| 符号 | 含义 | 代码对应 |
|------|------|---------|
| $S(\ell)$ | 标签 $\ell$ 的综合加权得分 | `fusion/fusion_engine.py::FusionEngine.fuse` |
| $\ell^*$ | 加权得分最高的标签 $\arg\max_\ell S(\ell)$ | `FusionOutput.label`（未命中 `low_confidence` / `conflict` 时） |
| $S(\ell^*)$ | 瞬时置信度（截断到 $[0,1]$） | `FusionOutput.confidence` |
| $E$ | 环境可信度因子 | `EnvContext.env_score` |
| $w_i(E)$ | 动态权重函数 | `fusion/weights.py` |
| $P_i(\ell)$ | 第 $i$ 路对标签 $\ell$ 的概率（**完整分布**） | `PerceptionResult.prob_dist` |
| $P_i(\ell^*)$ | 第 $i$ 路 top 标签的概率（标量） | `PerceptionResult.prob` |
| $P_{\text{expression}}$ | 表情识别概率分布 | `agents/expression/` |
| $P_{\text{behavior}}$ | 行为识别概率分布 | `agents/behavior/` |
| $E_0$ | logistic 中心点 | `configs/thresholds.yaml: weights.e0` |
| $k$ | logistic 斜率 | `configs/thresholds.yaml: weights.k` |
| — | 冲突判定阈值（首位与次位得分之差） | `configs/thresholds.yaml: negotiation.conflict_margin` |

---

*本文档为算法与数学模型的技术说明。修改本文档须与代码实现同步，
涉及接口变更时按 `CONTRIBUTING.md` 走三方评审。*
