"""发起人管理台的**内容声明**（纯数据 + 纯逻辑，无 GUI 依赖）。

这个模块要解决的问题
--------------------
需求文档给管理台开了很长一张清单（§模块三.1~4 的房间与权限、§模块三.2 的感知参数、
§模块四 的三条提醒、§模块五 的运行模式与加密存储）。但**后端只接通了其中一项**
（房间公开规则，``POST /room``）。

一条清单里混着「能用的」和「还没做的」，最容易出的错是**把没做的也画成能用的**：
放一个开关、点了没反应，用户会以为系统坏了；放一个假值，用户会拿着假数据做决定。
两种都比「明确写未接入」更糟。

所以这里给每个条目一个 :data:`KIND_WIRED` / :data:`KIND_READONLY` / :data:`KIND_FACT` /
:data:`KIND_UNWIRED` 的**诚实分类**，并附上它依据需求文档的哪一条。界面只负责按类渲染，
不负责决定「这项算不算做完了」—— 那是这里的判定，也是 ``tests/app/present/test_console.py``
钉住的东西。

四种 kind 的区别
----------------
====================  ==========================  ================================
kind                  界面表现                    含义
====================  ==========================  ================================
``wired``             可操作的控件                **操作真的会生效**（后端有写路径）
``readonly``          展示真实值，无控件          有真值可看，但**后端没有写路径**
``fact``              一句声明，无控件            设计上**已经是这样**，没有开关可言
``unwired``           灰块 + 「未接入」标签       后端尚未实现，界面**明确留白**
====================  ==========================  ================================

``readonly`` 与 ``unwired`` 的界线
-----------------------------------
判据是「**有没有真值可展示**」：
房间名与邀请码由服务端下发，是**真的**，所以 ``readonly``（缺的只是「改」这个动作）；
分享链接、权限转交、灵敏度这些连数据源都没有，所以 ``unwired``。
把前者也写成 ``unwired`` 会让用户以为房间名是假的；把后者写成 ``readonly`` 则等于
把「没做」粉饰成「做了但不可改」—— 两种都在骗人。

``fact`` 为什么不是「文案」
---------------------------
因为 ``fact`` 条目里的每一句都是**可被机器验证的设计断言**，不是宣传语。
``privacy.no_backdoor``（没有任何角色能绕过公开开关）在
``tests/app/present/test_console.py`` 里直接拿 ``visible_to`` 跑一遍验：
连发起人看别人的隐藏状态也必须是掩去的。文案与实现对不上时，那条测试会红 ——
这比在界面上写一句漂亮话可靠得多。

为什么 ``initiator_only`` 放在**数据**上而不是界面上
----------------------------------------------------
需求文档 §模块二.1：「所有用户通过同一链接/邀请码进入房间，页面基础布局完全一致」
「管理功能仅对发起人可见」。哪一块属于「管理功能」是**产品决策**，不是画法 ——
写进 GUI 就等于没有护栏（``app/web/`` 整体 omit 于覆盖率）。
所以这里给每个分区一个 ``initiator_only``，由 :func:`visible_sections` 统一裁决。
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "CONSOLE_SECTIONS",
    "KIND_FACT",
    "KIND_READONLY",
    "KIND_TEXT",
    "KIND_UNWIRED",
    "KIND_WIRED",
    "KINDS",
    "ConsoleItem",
    "ConsoleSection",
    "console_items",
    "item",
    "unwired_items",
    "visible_sections",
]

#: 界面可操作，且**操作真的会生效**（后端有写路径）。
KIND_WIRED = "wired"
#: 有真实值可展示，但后端**没有写路径**（缺的是「改」这个动作）。
KIND_READONLY = "readonly"
#: 设计上已经是这样，没有开关可言。这类条目的文案是**可被测试验证的断言**。
KIND_FACT = "fact"
#: 后端尚未实现，界面明确留白。
KIND_UNWIRED = "unwired"

KINDS: tuple[str, ...] = (KIND_WIRED, KIND_READONLY, KIND_FACT, KIND_UNWIRED)

#: 四种 kind 的界面标签文案。放在这里而不是 UI 层：它是**展示规则**，
#: 与配色同级，而 UI 外壳没有覆盖率护栏。
KIND_TEXT: dict[str, str] = {
    KIND_WIRED: "已接通",
    KIND_READONLY: "只读",
    KIND_FACT: "已生效",
    KIND_UNWIRED: "未接入",
}


@dataclass(frozen=True)
class ConsoleItem:
    """管理台里的一条。

    Attributes:
        key: 稳定标识（点分命名，如 ``room.publish_policy``）。界面的操作回调按它分发，
            所以它必须唯一且不随文案变化 —— 文案会改，key 不会。
        title: 中文标题。
        kind: :data:`KIND_WIRED` / :data:`KIND_READONLY` / :data:`KIND_FACT` /
            :data:`KIND_UNWIRED` 之一。
        hint: 一句话说明「这项做什么」，或「为什么还没有」。``unwired`` 条目
            **必须**写清楚缺的是什么，而不是只写「暂不支持」。
        doc_ref: 依据需求文档的哪一条。留空表示这是实现细节而非文档要求 ——
            当前清单里没有这种条目，但不排除将来有。
    """

    key: str
    title: str
    kind: str
    hint: str
    doc_ref: str = ""

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"kind must be one of {KINDS}, got {self.kind!r}")
        if not self.key:
            raise ValueError("key must not be empty")
        if not self.hint:
            raise ValueError(f"{self.key}: hint must not be empty")
        if self.kind == KIND_UNWIRED and not self.doc_ref:
            # 未接入条目必须能指出「哪条需求没做」—— 否则它只是一句推诿。
            raise ValueError(f"{self.key}: unwired items must cite a doc_ref")

    @property
    def actionable(self) -> bool:
        """界面上是否应该有可操作的控件（只有 ``wired`` 才有）。"""
        return self.kind == KIND_WIRED

    @property
    def kind_text(self) -> str:
        """该 kind 的中文标签。"""
        return KIND_TEXT[self.kind]


@dataclass(frozen=True)
class ConsoleSection:
    """管理台的一个分区。

    Attributes:
        key: 稳定标识。
        title: 分区标题。
        hint: 一句话说明这个分区管什么。
        initiator_only: 是否**仅对发起人可见**（需求文档 §模块二.1）。
            ``False`` 表示所有人都会看到这个分区。
        items: 分区内的条目，顺序即展示顺序。
    """

    key: str
    title: str
    hint: str
    initiator_only: bool
    items: tuple[ConsoleItem, ...]

    def __post_init__(self) -> None:
        if not self.items:
            raise ValueError(f"{self.key}: a section must not be empty")


#: 管理台的全部内容。顺序即页面上的展示顺序：先「房间」（最常用），
#: 再「感知参数」，再「交互提醒」，最后「隐私保障」（所有人可见，放最后当收尾）。
CONSOLE_SECTIONS: tuple[ConsoleSection, ...] = (
    ConsoleSection(
        key="room",
        title="房间与邀请",
        hint="房间叫什么、怎么把人拉进来、谁能留下。",
        initiator_only=True,
        items=(
            ConsoleItem(
                key="room.name",
                title="房间名称",
                kind=KIND_READONLY,
                hint="展示服务端下发的真实房间名；改名入口未接入。",
                doc_ref="§模块三.1",
            ),
            ConsoleItem(
                key="room.topic",
                title="学习主题",
                kind=KIND_READONLY,
                hint="展示服务端下发的真实主题；编辑入口未接入。",
                doc_ref="§模块三.1",
            ),
            ConsoleItem(
                key="room.invite_code",
                title="邀请码",
                kind=KIND_READONLY,
                hint="真实邀请码由服务端下发；未生成时显示「未生成」，不编造。",
                doc_ref="§模块三.1",
            ),
            ConsoleItem(
                key="room.share_link",
                title="分享链接",
                kind=KIND_UNWIRED,
                hint="缺的是一个可被其它设备访问的房间地址 —— 当前 hub 只监听 127.0.0.1。",
                doc_ref="§模块三.1",
            ),
            ConsoleItem(
                key="room.remove_member",
                title="移除参与者",
                kind=KIND_UNWIRED,
                hint="缺的是在线表的删除端点与「被移除」的客户端处理。",
                doc_ref="§模块三.1",
            ),
        ),
    ),
    ConsoleSection(
        key="policy",
        title="公开规则与权限",
        hint="谁能看见谁的状态、管理权在谁手上。",
        initiator_only=True,
        items=(
            ConsoleItem(
                key="room.publish_policy",
                title="状态公开规则",
                kind=KIND_WIRED,
                hint="可选「个人自主选择」或「全员强制公开」——改动立刻对全房间生效。",
                doc_ref="§模块三.2",
            ),
            ConsoleItem(
                key="room.transfer_temp",
                title="临时转交管理权限",
                kind=KIND_UNWIRED,
                hint="缺的是「权限」这一层的建模 —— 当前只有发起人这一个静态标志。",
                doc_ref="§模块三.3",
            ),
            ConsoleItem(
                key="room.transfer_perm",
                title="永久转交管理权限",
                kind=KIND_UNWIRED,
                hint="同上；永久转交还要处理原发起人退场后的房间归属。",
                doc_ref="§模块三.3",
            ),
            ConsoleItem(
                key="room.export",
                title="学习数据与复盘报告",
                kind=KIND_UNWIRED,
                hint="缺的是历史数据的落盘 —— 当前在线表与会话同生命周期，退出即散。",
                doc_ref="§模块三.4",
            ),
        ),
    ),
    ConsoleSection(
        key="perception",
        title="感知参数",
        hint="三个智能体怎么融合、跑多快、数据落在哪。",
        initiator_only=True,
        items=(
            ConsoleItem(
                key="perception.sensitivity",
                title="状态检测灵敏度",
                kind=KIND_UNWIRED,
                hint="缺的是「高/中/低」到融合阈值与平滑窗口的映射表，以及下发通道。",
                doc_ref="§模块三.2",
            ),
            ConsoleItem(
                key="perception.adaptive",
                title="环境自适应",
                kind=KIND_UNWIRED,
                hint="环境智能体已能产出光照/噪声质量，但还没有拿它去调另外两路的权重。",
                doc_ref="§模块三.2",
            ),
            ConsoleItem(
                key="perception.mode",
                title="运行模式",
                kind=KIND_UNWIRED,
                hint="「性能优先 / 平衡 / 省电」三档缺的是降采样与推理间隔的调度层。",
                doc_ref="§模块五",
            ),
            ConsoleItem(
                key="perception.encrypt",
                title="本地加密存储",
                kind=KIND_UNWIRED,
                hint="缺的是本地历史数据的落盘 —— 没有落盘，也就没有可加密的对象。",
                doc_ref="§模块五",
            ),
        ),
    ),
    ConsoleSection(
        key="interaction",
        title="交互与提醒",
        hint="状态出来了之后，谁该被提醒、以什么方式。",
        initiator_only=True,
        items=(
            ConsoleItem(
                key="notify.distract",
                title="分心提醒",
                kind=KIND_UNWIRED,
                hint="缺的是提醒通道（桌面通知 / 页面提示）与「多久算持续分心」的判定。",
                doc_ref="§模块四",
            ),
            ConsoleItem(
                key="notify.confused",
                title="困惑响应机制",
                kind=KIND_UNWIRED,
                hint="缺的是「困惑持续多久才值得打断」的策略，以及把信号推给谁的编排。",
                doc_ref="§模块四",
            ),
            ConsoleItem(
                key="notify.all",
                title="全员状态提醒",
                kind=KIND_UNWIRED,
                hint="缺的是发起人主动广播的通道 —— 当前只有读端点，没有广播端点。",
                doc_ref="§模块四",
            ),
        ),
    ),
    ConsoleSection(
        key="privacy",
        title="隐私保障",
        hint="这些不是待办，是当前实现**已经成立**的事实。",
        initiator_only=False,
        items=(
            ConsoleItem(
                key="privacy.no_backdoor",
                title="没有任何角色能绕过公开开关",
                kind=KIND_FACT,
                hint="可见性裁剪在服务端按观看者执行，发起人看别人也一样被掩去。",
                doc_ref="§模块二.2 / §模块六",
            ),
            ConsoleItem(
                key="privacy.local_inference",
                title="感知推理全程在本机完成",
                kind=KIND_FACT,
                hint="三路智能体与融合都在端侧跑，房间收到的只有状态标签与置信度。",
                doc_ref="§模块六",
            ),
            ConsoleItem(
                key="privacy.minimal_output",
                title="数据最小化输出",
                kind=KIND_FACT,
                hint="不出图、不出声、不传原始帧；线路格式里没有任何图像字段。",
                doc_ref="§模块六",
            ),
        ),
    ),
)


def visible_sections(*, initiator: bool) -> list[ConsoleSection]:
    """按观看者是否发起人，返回应展示的分区（需求文档 §模块二.1）。

    这是**权限判定**，所以它在这里而不在界面里：``app/web/`` 整体 omit 于覆盖率，
    判定写在那儿等于没有护栏。界面只拿到一个已经筛好的列表。
    """
    return [section for section in CONSOLE_SECTIONS if initiator or not section.initiator_only]


def console_items() -> list[ConsoleItem]:
    """全部条目拍平（顺序 = 分区顺序 × 分区内顺序）。"""
    return [entry for section in CONSOLE_SECTIONS for entry in section.items]


def unwired_items() -> list[ConsoleItem]:
    """只取「后端尚未实现」的条目 —— 用于页面顶部的诚实计数。"""
    return [entry for entry in console_items() if entry.kind == KIND_UNWIRED]


def item(key: str) -> ConsoleItem | None:
    """按 key 查条目；不存在时返回 ``None``（不抛错，同展示层的兜底约定）。"""
    return next((entry for entry in console_items() if entry.key == key), None)
