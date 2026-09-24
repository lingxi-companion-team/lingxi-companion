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

**接口契约、融合层、多端展示地基与环境智能体（规则版）已落地；表情、行为两路与流水线仍在开发中**：

- 已建立 `main`、`develop` 两条常驻分支，并配置 ruleset 分支保护与 5 项 CI 检查；
- 已冻结统一感知数据结构（`common/perception_types.py`）与 mock 数据目录；
- 已实现环境驱动动态权重融合、三级置信度协商与“3 中 2”时序平滑（`fusion/`）；
- 已实现多端展示地基 `app/`：参与者信封 → 纯函数展示层 → 按观看者裁剪的只读快照端点；
- 已实现环境智能体 `agents/env/`：以亮度因子 × 清晰度因子合成环境可信度 $E$，
  遮挡只上报不参与合成（规则版，模型版待替换）；
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
├── app/                 # 多端展示地基（信封 / 展示层 / 只读快照端点）
│   ├── envelope.py      #   参与者信封：会话层身份与可见性
│   ├── hub.py           #   在线表 + 按观看者裁剪 + 只读 HTTP 端点
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

## 文档入口

- [算法技术文档](docs/algorithm.md) —— 融合权重、协商分级、时序平滑与统一标签体系
- [数据来源与评测口径](docs/datasets.md) —— 各智能体用哪些数据集、许可限制、划分与基线
- [贡献指南](CONTRIBUTING.md)

## 隐私说明

项目涉及视频和学习状态信息。开发阶段默认不提交原始视频、个人身份信息、模型训练缓存或本地环境目录。真实数据采集、保存和共享应在取得必要授权并完成脱敏后进行。
