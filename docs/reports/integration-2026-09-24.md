# 串行集成联调记录（首跑）

> 日期：2026-09-24 ｜ 主责：C（集成与发布汇总人）｜ 入口：`python -m app.integration`
> 代码落点：`app/integration/`（`session.py` / `sources.py` / `__main__.py`）
> 对应任务计划：**3.2「先跑通串行版」**

## 0. 结论摘要

| 问题 | 结论 |
|:--|:--|
| 串行链路跑通了吗？ | **跑通** —— 8 组命令（7 个 mock 场景 × 2 个输入源 × 2 种角色）全绿，退出码 0 |
| 谁在跑？ | 环境路是**真实实现**（`RuleBasedEnvAgent`）；表情 / 行为两路是 `common/mock` 假智能体 |
| 因此能下什么结论？ | 只能下「**链路装配正确 + 环境路可用**」。**不能**下任何模型精度结论 |
| 冲突场景怎么表现？ | 4 帧全部 `unknown(conflict)`，确认 0 帧 —— **拒判而不强判**，符合设计 |
| 抖动场景怎么表现？ | 融合层照样命中 `consensus`，但平滑层**不确认**、维持上一稳定状态并置 `stale` |
| 慢吗？ | 感知流路径每帧 <1 ms；全链路路径的环境路约 1.5~2.6 ms（合成帧口径，见 §5.6） |
| 本轮发现并修掉的缺陷 | **重复播放场景时时间戳会倒退**（见 §6） |
| 与 `pipeline/` 是什么关系？ | 本链路是**串行基线**，不替代并行流水线；两者语义与依赖都不同（§1） |

⚠️ **本记录不含真实模型**：表情与行为两路尚未交付。脚本启动时会**明文告警**这一点，
以免演示时把常量输出误当成模型输出。

## 1. 边界：为什么放在 `app/integration/` 而不是 `pipeline/`

| | `app/integration/`（本次落地） | `pipeline/`（B 主责） |
|:--|:--|:--|
| 调度方式 | **串行、阻塞**：一帧跑完再下一帧 | 多路并发 + 队列 + 丢弃过期帧 |
| 用途 | CLI / 演示可用性、联调与回归基线 | 200ms 指标与吞吐 |
| 依赖 | **零第三方依赖**（CI 无 numpy） | numpy / torch / onnxruntime |
| 任务计划对应项 | 3.2 先跑通串行版（C 主导） | 3.4 升级为并行版（B） |

串行版先行的理由是任务计划 §四 的既定应对：「多进程框架受阻 → 先完成单线程串行
版本；B 在独立 feature 分支优化，**不阻塞主线**」。因此本链路是**兜底基线**：
任何一环没交付，它仍能跑通并给出可判读的输出。

## 2. 落点与职责

| 文件 | 职责 | 覆盖率 |
|:--|:--|:--|
| `app/integration/session.py` | `SerialSession`：装配三路 + 融合 + 平滑，逐路 `try/except` 降级，产出 `FrameReport` | 100% |
| `app/integration/sources.py` | 两类输入源（感知流 / 原始帧）+ `SyntheticFrame` | 100% |
| `app/integration/__main__.py` | CLI：参数、逐帧打印、汇总、`--json`、`--serve` | 100% |
| `tests/app/test_integration_session.py` | 装配与韧性（降级、信封、输入源边界） | — |
| `tests/app/test_integration_cli.py` | CLI 的 stdout 契约与退出码 | — |

**合成帧只有一份**：`SyntheticFrame` 定义在 `app/integration/sources.py`，由
`tests/stress/stresskit.py` 导入后原样导出。它有两个消费者（无摄像头时的全链路
演示、压测载荷），两处各写一份必然分叉 —— 演示看到的画面与压测压的画面会悄悄
变成两个东西。

## 3. 复现方式

在仓库根目录执行，只需标准库：

```bash
python -m app.integration --help
python -m app.integration --scenario consensus --frames 6 --quiet
python -m app.integration --source synthetic --frames 4 --quiet
python -m app.integration --scenario conflict --frames 4 --json --quiet
```

## 4. 实测输出

以下为 2026-09-24 在开发机上的**真实输出**（`--quiet` 只抑制信息级日志，
告警仍会输出）。每行格式：

```
#帧号 ts=时间戳 E=环境因子 三路结果 -> 融合标签(命中的协商级别) final=平滑后状态[/stale]
```

### 4.1 共识场景（`--scenario consensus --frames 6`）

```text
#0001 ts=  0.10 E=0.92 expression=focused:0.92 behavior=focused:0.88 -> focused(consensus) final=-
#0002 ts=  0.20 E=0.92 expression=focused:0.92 behavior=focused:0.88 -> focused(consensus) final=-
#0003 ts=  0.30 E=0.92 expression=focused:0.92 behavior=focused:0.88 -> focused(consensus) final=focused
#0004 ts=  0.40 E=0.92 expression=focused:0.92 behavior=focused:0.88 -> focused(consensus) final=focused
#0005 ts=  0.50 E=0.92 expression=focused:0.92 behavior=focused:0.88 -> focused(consensus) final=focused
#0006 ts=  0.60 E=0.92 expression=focused:0.92 behavior=focused:0.88 -> focused(consensus) final=focused
—— 共 6 帧；确认状态 4 帧；降级 0 帧；最终 focused
```

### 4.2 冲突场景（`--scenario conflict --frames 4`）

```text
#0001 ts=  0.10 E=0.60 expression=confused:0.90 behavior=focused:0.90 -> unknown(conflict) final=-
#0002 ts=  0.20 E=0.60 expression=confused:0.90 behavior=focused:0.90 -> unknown(conflict) final=-
#0003 ts=  0.30 E=0.60 expression=confused:0.90 behavior=focused:0.90 -> unknown(conflict) final=-
#0004 ts=  0.40 E=0.60 expression=confused:0.90 behavior=focused:0.90 -> unknown(conflict) final=-
—— 共 4 帧；确认状态 0 帧；降级 0 帧；最终 -
```

### 4.3 弱光场景（`--scenario low_light --frames 4`）

```text
#0001 ts=  0.10 E=0.30 expression=confused:0.60 behavior=distracted:0.65 -> distracted(reweight) final=-
#0002 ts=  0.20 E=0.30 expression=confused:0.60 behavior=distracted:0.65 -> distracted(reweight) final=-
#0003 ts=  0.30 E=0.30 expression=confused:0.60 behavior=distracted:0.65 -> distracted(reweight) final=distracted
#0004 ts=  0.40 E=0.30 expression=confused:0.60 behavior=distracted:0.65 -> distracted(reweight) final=distracted
—— 共 4 帧；确认状态 2 帧；降级 0 帧；最终 distracted
```

### 4.4 遮挡场景（`--scenario occluded --frames 4`）

```text
#0001 ts=  0.10 E=0.75 expression=focused:0.88 behavior=confused:0.86 -> confused(reweight) final=-
#0002 ts=  0.20 E=0.75 expression=focused:0.88 behavior=confused:0.86 -> confused(reweight) final=-
#0003 ts=  0.30 E=0.75 expression=focused:0.88 behavior=confused:0.86 -> confused(reweight) final=confused
#0004 ts=  0.40 E=0.75 expression=focused:0.88 behavior=confused:0.86 -> confused(reweight) final=confused
—— 共 4 帧；确认状态 2 帧；降级 0 帧；最终 confused
```

### 4.5 抖动场景（`--scenario jitter --frames 6`）

```text
#0001 ts=  0.10 E=0.90 expression=focused:0.80 behavior=focused:0.78 -> focused(consensus) final=-
#0002 ts=  0.20 E=0.90 expression=focused:0.80 behavior=focused:0.78 -> focused(consensus) final=-
#0003 ts=  0.30 E=0.90 expression=focused:0.90 behavior=focused:0.90 -> focused(consensus) final=focused
#0004 ts=  0.40 E=0.90 expression=confused:0.90 behavior=confused:0.90 -> confused(consensus) final=focused
#0005 ts=  0.50 E=0.90 expression=distracted:0.90 behavior=distracted:0.90 -> distracted(consensus) final=focused/stale
#0006 ts=  0.60 E=0.90 expression=focused:0.80 behavior=focused:0.78 -> focused(consensus) final=focused/stale
—— 共 6 帧；确认状态 2 帧；降级 0 帧；最终 focused
```

### 4.6 全链路 + 合成帧（`--source synthetic --frames 4`）

```text
#0000 ts=  0.00 E=1.00 expression=focused:0.85(0.03ms) behavior=focused:0.80(0.01ms) env=E1.00(2.57ms) -> focused(consensus) final=-
#0001 ts=  0.10 E=1.00 expression=focused:0.85(0.03ms) behavior=focused:0.80(0.01ms) env=E1.00(2.16ms) -> focused(consensus) final=-
#0002 ts=  0.20 E=1.00 expression=focused:0.85(0.02ms) behavior=focused:0.80(0.01ms) env=E1.00(1.88ms) -> focused(consensus) final=focused
#0003 ts=  0.30 E=1.00 expression=focused:0.85(0.01ms) behavior=focused:0.80(0.01ms) env=E1.00(1.89ms) -> focused(consensus) final=focused
—— 共 4 帧；确认状态 2 帧；降级 0 帧；最终 focused
```

### 4.7 镜头被挡（`--source synthetic --texture flat --frames 3`）

```text
#0000 ts=  0.00 E=0.00 expression=focused:0.85(0.04ms) behavior=focused:0.80(0.01ms) env=E0.00(1.69ms) -> focused(reweight) final=-
#0001 ts=  0.10 E=0.00 expression=focused:0.85(0.08ms) behavior=focused:0.80(0.02ms) env=E0.00(1.99ms) -> focused(reweight) final=-
#0002 ts=  0.20 E=0.00 expression=focused:0.85(0.02ms) behavior=focused:0.80(0.01ms) env=E0.00(1.47ms) -> focused(reweight) final=focused
—— 共 3 帧；确认状态 1 帧；降级 0 帧；最终 focused
```

### 4.8 教师端信封（`--role teacher --frames 3 --json`）

```text
—— 共 3 帧；确认状态 1 帧；降级 0 帧；最终 -
{
  "participant_id": "s01",
  "role": "teacher",
  "hidden": false,
  "ts": 0.3,
  "state": null
}
```

## 5. 逐条判读

### 5.1 「3 中 2」窗口确实需要三帧（§4.1）

前两帧 `final=-`（尚无任何可对外输出的内容），第三帧起确认。这是
`TemporalSmoother` 的窗口语义：**不足票数时不输出 UNKNOWN，而是保持上一稳定状态**
（从未确认过则输出 `None`）。若按旧语义返回 UNKNOWN，演示时状态码会在开头闪断。

### 5.2 冲突被识别为「无法定论」，而不是随便挑一个（§4.2）

两路判断相反、置信度相当（E=0.60 恰是 logistic 中心点 → 权重 0.5 : 0.5），
加权得分近乎持平 → `reason="conflict"` → 输出 `unknown`。**4 帧一次都没有确认**，
这正是设计意图：强行选一个会引入与语义相关的系统性偏置（例如总偏向字典序靠前的
标签），比拒判更糟。

> 注意 `降级 0 帧` 与 `确认 0 帧` 是两件事：前者指**没有环节出错**，
> 后者指**没有形成可用判定**。拒判是正常行为，不是故障。

### 5.3 弱光把判断权转给行为通道（§4.3）

E=0.30 远低于中心点 → 行为权重显著高于表情 → 输出跟随行为路的 `distracted`。
表情路单独看是 `confused`，但它在环境差时不应主导 —— 这就是环境驱动动态加权的
实际效果，也是本链路第一次在**端到端**上把它跑出来。

### 5.4 遮挡只降权、不重复计入（§4.4）

遮挡 0.95 触发融合层的表情降权，表情路的 `focused:0.88` 被压掉，结果跟随行为路的
`confused`。环境路的 `occlusion_weight` 保持 0，遮挡只作为观测量上报 ——
若它也参与 $E$ 的合成，遮挡会被计入两次，补偿强度失控。

### 5.5 融合「共识」**不等于**平滑「确认」（§4.5）★

这是本记录最值得记下的一条：

- 第 4~6 帧融合层照样命中 `consensus`（环境好 + 两路一致），**瞬时**标签是
  `confused` / `distracted`；
- 但平滑层看的是**窗口票数**：第 5、6 帧的窗口是 `F/C/D`（各一票），无多数 →
  不确认 → `final=focused/stale`。

即**两级输出回答不同问题**：融合层答「这一帧是什么」，平滑层答「现在能否对外宣布
状态已改变」。前端必须区分二者，否则会在抖动时把状态码刷成噪声。

### 5.6 全链路三路真实执行（§4.6 / §4.7）

`--source synthetic` 走的是**原始帧**入口：三路都真实执行，环境路对合成帧算出
E=1.00（噪声纹理，细节充足）；换成 `flat`（整帧同色）后 E=0.00，融合级别随之从
`consensus` 降为 `reweight`。

⚠️ **`env=` 那一列的 1.5~2.6 ms 不能当作真实性能**：合成帧的像素是纯 Python
按需算出来的，逐点访问比 numpy 慢约两个数量级。可移植的口径见
[`stress-2026-09-24.md`](stress-2026-09-24.md) §1.3：环境智能体的**度量与合成**
部分是 **0.80 ms**，且与分辨率无关。

### 5.7 教师不占格（§4.8）

`--role teacher` 时逐帧仍在算（`final=focused` 出现在每帧报告里），但**下发给前端的
信封里 `state` 恒为 `null`**。教师端消费的是全班汇总（`app.present.summary`），
不是自己的感知结果 —— 这与设计稿 A1「教师不占格」一致。

## 6. 本轮修复的缺陷：重复播放场景时时间戳倒退

**现象**：`--frames` 大于场景长度时会把场景重复播放。首次实测发现时间戳形如
`0.10 → 0.20 → 0.30 → 0.40 → 0.10 → 0.20`（第二轮从 0.10 重新开始）。

**为什么必须修**：mock 场景自带 `ts`（0.1 起）与 `frame_id`（1 起），直接原样重放
就是**时间倒退**。而「ts 单调递增」是整条链路默认的前提 —— 融合层不读它，
但平滑层的 `_last_ts`、以及后续要接入的延迟统计都依赖它。这类问题在单测里
不会暴露（每帧独立断言），只有在「跑一段」时才会显形。

**修法**：`MockScenarioSource` 在重复时**整体平移** ts 与 frame_id，步长取场景
自身的最小时间间隔（`_time_step()`），因此重复播放表现为一段连续的长视频。
上面 §4.1 的 6 帧输出即为修复后：`0.10 → 0.60` 单调，帧号 `1 → 6` 不重复。
退化情形（场景内所有帧同刻，推不出间隔）退化为 1 秒步长，已有测试覆盖。

## 7. 已知缺口与下一步

| 缺口 | 影响 | 谁 / 何时 |
|:--|:--|:--|
| 表情 / 行为两路是假智能体 | 无法得出任何精度结论 | A、B 模型交付后替换 `build_session()` 里的两行 |
| 摄像头输入未接 | 只能跑合成帧与 mock | C2（桌面客户端 `app/client/`）承担 `cv2.VideoCapture` 适配 |
| 前端只验证到信封与只读端点 | 没有真实界面 | C2（Tkinter） |
| 全程无并发 | 只能证明「跑得通」，不能证明「跑得快」 | B 的 `pipeline/`（任务 3.4） |
| `--serve` 只提供只读快照 | 无写入、无鉴权 | 课堂内网、几十人规模足够；扩容时需重新评估 |

## 8. 与其它报告的关系

- 性能口径：本记录**不重复**压测数字，一律引用
  [`stress-2026-09-24.md`](stress-2026-09-24.md)；
- 数学与算法：`docs/algorithm.md` §3（融合）、§4（平滑）；
- 展示层契约（信封 / 占格 / 可见性）：`docs/algorithm.md` §1.1 与 `app/envelope.py` 的说明。
