# 灵犀学伴（Lingxi Companion）

> 面向在线学习边缘场景的仿生多智能体轻量情感协同感知系统
>
> 江苏省大学生创新创业训练计划项目

## 项目简介

本仓库用于开发“灵犀学伴”原型系统。项目面向在线学习场景，计划在普通终端设备上通过本地摄像头完成表情、行为和环境感知，并使用动态权重融合与时序平滑输出稳定的教学状态。

系统重点关注：

- 多智能体协同感知；
- 轻量化、可离线运行的模型；
- 弱光、遮挡等复杂环境下的鲁棒性；
- 端侧本地处理和数据最小化；
- 实时视频分析与状态可视化。

## 当前状态

**接口契约、融合层、多端展示（含桌面客户端）、环境智能体（规则版）与串行集成入口已落地；表情、行为两路与流水线仍在开发中**：

- 已建立 `main`、`develop` 两条常驻分支，并配置 ruleset 分支保护与 5 项 CI 检查；
- 已冻结统一感知数据结构（`common/perception_types.py`）与 mock 数据目录；
- 已实现环境驱动动态权重融合、三级置信度协商与“3 中 2”时序平滑（`fusion/`）；
- 已实现多端展示地基 `app/`：参与者信封 → 纯函数展示层 → 按观看者裁剪的快照端点
  与 `POST /hidden` 写通道（“对同学隐藏”开关的上报口）；
- 已实现**桌面客户端** `app/client/`（Tkinter，标准库零依赖）：课堂共享宫格 + 教师汇总面板，
  一个窗口在展开态与最小化态之间切换（最小化后是可拖动的圆形图标 + 状态分量气泡，
  右键菜单与 `Ctrl+Alt+Q` 均可退出）；入口 `python -m app.client`
  （交付记录见 `docs/reports/client-2026-09-24.md`）；
- 已实现环境智能体 `agents/env/`：以亮度因子 × 清晰度因子合成环境可信度 $E$，
  遮挡只上报不参与合成（规则版，模型版待替换）；
- 已实现**串行集成主流程** `app/integration/`：帧源（合成帧 / mock 流）→ 三路智能体 → 融合
  → 平滑 → 前端信封，含逐路降级、日志与 `python -m app.integration` 命令行入口
  （联调记录见 `docs/reports/integration-2026-09-24.md`）；
- `agents/expression`、`agents/behavior` 与 `pipeline/` 目前仍是空壳；
- 尚未提交真实模型、原始视频数据或训练权重。

日常开发从 `develop` 创建短期的 `feature/*` 分支，合并后即删除。

## 分支约定

```text
main       稳定、可演示、可发布版本
develop    日常集成分支
feature/*  从 develop 创建的短期功能分支（用完即删）
release/*  发布候选分支
hotfix/*   已发布版本的紧急修复分支
```

日常开发建议：

```text
feature/* → develop → release/* → main
```

在成员职责尚未确定前，使用 `feature/<topic>` 这样的中性分支名；职责确定后，再按照任务分配计划命名和维护功能分支。

## 项目目录

```text
lingxi-companion/
├── agents/
│   ├── behavior/       # 行为智能体（空壳）
│   ├── expression/     # 表情智能体（空壳）
│   └── env/            # 环境智能体（规则版已实现）
│       └── agent.py     #   E 值评估：采样 / 度量 / 合成 / 协议适配
├── app/                 # 应用层：多端展示 + 串行集成
│   ├── client/          #   桌面客户端（Tkinter 外壳；规则全在 present/，本层只画）
│   ├── envelope.py      #   参与者信封：会话层身份与可见性
│   ├── hub.py           #   在线表 + 按观看者裁剪 + HTTP 端点（读快照 / 写隐藏开关）
│   ├── integration/     #   串行集成主流程（帧源 → 三路 → 融合 → 平滑 → 信封）
│   └── present/         #   展示纯函数：宫格 / 汇总 / 气泡 / 配色 / 可见性
├── common/              # 共享冻结层（接口契约所在，变更须三方评审）
│   ├── agent_base.py    #   智能体抽象基类
│   ├── config.py        #   零依赖配置解析
│   ├── input_spec.py    #   表 B：感知输入规格（frame 契约）
│   ├── label_mapping.py #   表 A：上游类别 → 情感分布
│   ├── mock/            #   mock 数据生成器
│   └── perception_types.py  # 唯一接口契约
├── configs/             # 阈值配置（集中全部可调参数）
├── fusion/              # 动态权重融合、时序平滑
├── pipeline/            # 采集、推理、传输流水线
├── tests/               # 契约/冒烟测试 + 各模块单测与压测
├── docs/                # 算法与数据文档（见下方「文档入口」）
└── scripts/             # 工具脚本
```

> 各目录的详细职责与人员分工见团队内部协作文档（不入库）。

## 本地运行

建议使用 Python 3.10 或更高版本创建虚拟环境：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
pip install -r requirements.txt -r requirements-dev.txt
pytest -q
```

依赖分成两个文件，各有明确分工：

| 文件 | 内容 | 谁需要装 |
|:--|:--|:--|
| `requirements.txt` | 运行时库：`numpy`、`torch`、`onnxruntime`、`opencv-python` | 本地开发 / 部署 |
| `requirements-dev.txt` | 工具链：`ruff`、`mypy`、`pytest`、`pytest-cov` | 本地开发 / **CI** |

CI 只安装 `requirements-dev.txt`，因此运行时库的体积不会拖慢门禁检查。
`requirements.txt` 默认挂官方 CPU-only 源安装 `torch` —— 本项目在普通 CPU 上
以端侧纯视觉方式运行，不需要 GPU，这样可以避开数 GB 的 CUDA 组件。

> `requirements.txt` 里的运行时依赖与 `pyproject.toml` 的 `[project] dependencies`
> 保持一致，两者改动要同步。若改用可编辑安装，请手动带上同一个 CPU 源 ——
> `pyproject.toml` 表达不了 pip 的索引选项：
>
> ```powershell
> pip install -e . --extra-index-url https://download.pytorch.org/whl/cpu
> ```

真实模型和端到端应用将在接口稳定后逐步加入。模型权重、原始视频和本地虚拟环境不提交到 Git 仓库。

### 跑通串行链路（无需任何第三方依赖）

串行集成入口只用标准库，**装完 `requirements-dev.txt` 就能跑**，不依赖摄像头或模型权重：

```powershell
python -m app.integration --help                          # 查看全部参数
python -m app.integration --scenario consensus --frames 6 # 回放 mock 感知流
python -m app.integration --source synthetic --frames 30  # 合成帧跑全链路（三路真实执行）
```

表情与行为两路尚未交付，默认由 `common/mock` 的假智能体顶替，运行时会**明文告警**；
环境路是真实实现。因此它的输出用于验证「链路装配与降级行为」，
**不代表**融合精度或端到端性能。

### 打开桌面客户端（Tkinter，同样零第三方依赖）

```powershell
python -m app.client --demo --role teacher          # 本机起演示 hub，直接开窗（教师端）
python -m app.client --demo --role student          # 学生端：宫格 + “仅对同学隐藏（教师仍可见）”
python -m app.client --url http://127.0.0.1:8765    # 连一个已在跑的 hub
python -m app.client --demo --selftest --json --port 0   # 冒烟自检：构建→绘制→切态→退出
```

`--demo` 会起一个**真实的本地 HTTP 服务**并用 `app.integration` 持续喂入 mock 数据：
宫格上的颜色与「维持中」角标都真的过了一遍融合与平滑，而不是随机涂色。
最小化后窗口变成一个可拖动的圆形图标，右键菜单与 `Ctrl+Alt+Q` 均可退出。

> 客户端代码整体排除在覆盖率之外（CI 无显示环境，跑不了 Tkinter），
> 所以**展示规则一律放在 `app/present/`**（纯函数、100% 覆盖），客户端只负责画。
> 交付记录与验收对照见 `docs/reports/client-2026-09-24.md`。

## 文档入口

- [算法技术文档](docs/algorithm.md) —— 融合权重、协商分级、时序平滑与统一标签体系
- [数据来源与评测口径](docs/datasets.md) —— 各智能体用哪些数据集、许可限制、划分与基线
- [贡献指南](CONTRIBUTING.md)

## 隐私说明

项目涉及视频和学习状态信息。开发阶段默认不提交原始视频、个人身份信息、模型训练缓存或本地环境目录。真实数据采集、保存和共享应在取得必要授权并完成脱敏后进行。
