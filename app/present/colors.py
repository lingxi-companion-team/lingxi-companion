"""状态 → 颜色 映射（纯逻辑，无 GUI 依赖）。

这里只产出**色值字符串**，不碰任何绘图库，因此可以在 headless CI 里满跑
（GUI 层 ``app/web/`` 整体 omit）。

v8 统一页配色（2026-10-01）
---------------------------
需求文档（通用网课场景版）§模块二.3 明确规定了四色的语义::

    绿色 = 专注    黄色 = 困惑    红色 = 分心    灰色 = 不确定 / 未公开

这一版把 v7 的「蓝紫锈灰」整体换成**绿黄红灰**，并**取消教师端/学生端分色**
（两端共用同一套色板）。

为什么一个状态要有两个色值（ink 与 fill）
------------------------------------------
「黄色」在浅底上有一个众所周知的硬约束：**纯黄当文字色永远过不了 4.5:1**。
实测 ``#f2c200`` 压白卡只有 1.68:1、压画布 1.55:1 —— 拿它写状态名等于没写。
反过来，把黄压暗到达标（``#876300``）就变成了橄榄/芥末色，**不再是「黄色」**，
而需求文档要的正是能被一眼认出的黄。

所以本模块把「一个状态的颜色」拆成**两个令牌**，各司其职：

==================  ==============================  ==========================
令牌                用途                            门槛
==================  ==============================  ==========================
``STATE_COLORS``    文字 / 色点 / 左边条 / 描边      WCAG 1.4.3 **4.5:1**（三底）
``STATE_FILLS``     颜色标签的**填充底色**            WCAG 1.4.11 **3:1**（压白卡）
==================  ==============================  ==========================

于是「绿黄红灰」在**标签底色**上保持饱和可辨（黄就是黄），而状态名文字走
深色 ink —— 两条通道各自达标，不互相拖累。

``#f2c200`` 的 3:1 怎么办
-------------------------
黄的 fill 压白卡只有 1.68:1，**单靠填色达不到图形门槛**。修法是给色标签加
**1px 的 ink 描边**（``#876300`` 压白卡 5.50:1）—— 边界由描边保证 ≥3:1，
填色只负责色相识别。这条规则在 ``test_contrast.py`` 里有断言，不是口头约定。

ink 为什么取「等权重」的一组
----------------------------
四个 ink 的对比度剖面被刻意做成几乎一致::

    状态        ink        压白 / 压画布 / 压面板
    focused     #1e7838    5.53 / 4.78 / 5.09
    confused    #876300    5.50 / 4.76 / 5.07
    distracted  #b74040    5.50 / 4.76 / 5.06
    unknown     #5b6a7f    5.51 / 4.77 / 5.07

好处是**没有哪个状态因为颜色更亮而显得更「重要」**。在浅底上，明度高的色会
天然前跳；若四个色明度不一，扫视时会把「亮」误读成「优先」，而这里四个状态
是并列的。真正该前跳的「分心」靠**形状 + 位置 + 文案**去抢注意力，
不靠偷偷调亮颜色 —— 那是会牺牲可达性的做法。

关于「为什么不用更扎眼的红/绿」
--------------------------------
四个 ink 都必须在**白卡 + 画布 + 面板**三个浅底上各自 ≥4.5:1。这决定了明度上限：
更亮的绿（``#2e7d32``）压画布只有 4.44、更亮的红（``#e5484d``）压白卡 3.91，
都掉出门槛。饱和的那一档因此被放到 :data:`STATE_FILLS` 里 —— **不是放弃饱和度，
而是把它放到不需要承载文字的通道上**。
"""

from __future__ import annotations

from common.perception_types import EmotionLabel

__all__ = [
    "CLOSED_COLOR",
    "DIM_COLOR",
    "ON_DARK_TEXT",
    "ON_LIGHT_TEXT",
    "STATE_COLORS",
    "STATE_FILLS",
    "color_for",
    "color_for_count",
    "color_for_key",
    "color_for_key_count",
    "fill_for",
    "fill_for_key",
    "is_dim",
    "on_fill",
]

#: 四个状态的 **ink 色**：承载文字、色点、左边条与描边。
#:
#: **形状/中文名是并行的第二、第三编码通道**（见 :mod:`app.present.shape`），
#: 所以这四个色值不必（也不该）承担「光靠颜色区分」的职责 —— 它们要保证的是
#: 「压在自己的底色上读得清」。下面每条的注释就是该色值实测的三个对比度。
STATE_COLORS: dict[EmotionLabel, str] = {
    # 5.53 压白 / 4.78 压画布 / 5.09 压面板。绿 —— 需求文档「绿色=专注」。
    EmotionLabel.FOCUSED: "#1e7838",
    # 5.50 压白 / 4.76 压画布 / 5.07 压面板。琥珀（黄压暗到达标的那一档）—— 「黄色=困惑」。
    EmotionLabel.CONFUSED: "#876300",
    # 5.50 压白 / 4.76 压画布 / 5.06 压面板。红 —— 「红色=分心」。
    EmotionLabel.DISTRACTED: "#b74040",
    # 5.51 压白 / 4.77 压画布 / 5.07 压面板。石板灰 —— 「灰色=不确定/未公开」。
    EmotionLabel.UNKNOWN: "#5b6a7f",
}

#: 四个状态的 **fill 色**：色标签（颜色标签/色块）的填充底。
#:
#: 门槛是 WCAG 1.4.11 的 **3:1**（压白卡）。``CONFUSED`` 的黄只有 1.68:1，
#: **达不到** —— 它必须靠 ink 描边补足边界对比（见模块 docstring）。
#: 这不是「凑合」，而是「纯黄在浅底上不可能同时满足色相识别与 3:1」这条
#: 物理事实的诚实处理：**填色给色相，描边给对比**。
#:
#: 与 :data:`STATE_COLORS` 同键同序，``test_colors.py`` 钉住了这一点。
STATE_FILLS: dict[EmotionLabel, str] = {
    # 3.21 压白卡 —— 单独过 3:1，无需描边也看得见。
    EmotionLabel.FOCUSED: "#2ea44f",
    # 1.68 压白卡 —— **必须**配 ink 描边，否则在白卡上等于没有边界。
    EmotionLabel.CONFUSED: "#f2c200",
    # 4.53 压白卡 —— 余量充足。
    EmotionLabel.DISTRACTED: "#dc3545",
    # 3.06 压白卡 —— 刚过线，余量小，故实际使用中同样建议配描边。
    EmotionLabel.UNKNOWN: "#8a94a6",
}

#: ``on_fill`` 的白色候选。
ON_LIGHT_TEXT = "#ffffff"
#: ``on_fill`` 的深色候选（比纯黑柔和）。
ON_DARK_TEXT = "#16233a"

#: 「已关闭感知」的展示色（v7 第 5 态，设计稿第 5 张 KPI 卡）。
#:
#: 语义与 :data:`STATE_COLORS` 里四个情感色**不同级**：它不是一个 ``EmotionLabel``
#: （是用户主动关掉采集，不是系统判定，见 :class:`app.envelope.ParticipantState.closed`），
#: 所以单列一个常量而不是塞进 ``STATE_COLORS``（那个 dict 的键恒为 4 个枚举成员，
#: ``test_colors.py`` 钉住了这一点）。
#:
#: 取值是比 ``UNKNOWN`` **更深**的中性石板灰：关闭是「确定的、非情感的状态」，
#: 该比「系统没测出来」更实、更沉，而不是更淡。实测 6.39 / 5.53 / 5.89，余量充足。
#: 之所以比 ``UNKNOWN`` 深而不是浅 —— 它与 ``DIM_COLOR``（「无人」的置灰）分工不同：
#: 那个是「该状态当前无人」的**图形**置灰（按 3:1 验），这个是**文字**色（按 4.5:1 验）。
CLOSED_COLOR = "#55606e"

#: 「该分量人数为 0」时的置灰色（设计稿 §10.1 d1）。
#:
#: 与 ``UNKNOWN`` 的 ``#626c79`` 分工不同：那个是「本帧未形成判定」，这个是
#: 「该状态当前无人」。两者都是灰但语义不同，同色会让教师误读，所以这里**更浅**。
#:
#: 取值受三条约束夹逼：① 必须比 ``UNKNOWN`` 浅，否则两者混同；②「未形成判定」与
#: 「无人」的**相互**对比也要看得出差别（≥1.3 就够了，它们不是并列读数）；
#: ③ 它会被画成一根**实心占比条**，那是图形元素，按 WCAG 1.4.11 要 ≥3:1。
#:
#: 三个候选实测（压白 / 压画布 / 压面板）::
#:
#:     #b8c2cf   1.80 / 1.66 / 1.58   条太淡，几乎看不见 → 弃
#:     #8f9bab   2.82 / 2.60 / 2.48   仍不到 3 → 弃
#:     #7d8b9a   3.79 / 3.50 / 3.36   三条全过，且与 UNKNOWN 相距 1.93 → **取此值**
#:     #626c79   —— 这是 UNKNOWN，不能再深
#:
#: 注：本值只用于**图形**（占比条、状态条）。若今后要让置灰分量也承载文字，
#: 3.79 远不够 4.5，必须另取更深的文字色。
DIM_COLOR = "#7d8b9a"


def color_for(label: EmotionLabel) -> str:
    """取状态色。

    未登记的标签**退回置灰色而不是抛错** —— 展示层不该因为一条异常数据就整个崩掉。
    """
    return STATE_COLORS.get(label, DIM_COLOR)


def is_dim(count: int) -> bool:
    """人数为 0 的分量置灰但仍占位（设计稿 §10.1 d1）。"""
    return count <= 0


def color_for_count(label: EmotionLabel, count: int) -> str:
    """按「状态 + 人数」取色：0 人的分量置灰。"""
    return DIM_COLOR if is_dim(count) else color_for(label)


def color_for_key(label: str) -> str:
    """按**线路格式的字符串键**取状态色。

    客户端从 payload 里拿到的是 ``"focused"`` 这样的字符串而不是枚举，所以需要一个
    str 入口。未登记的键退回置灰色而不是抛错 —— 与 :func:`color_for` 同一取舍：
    展示层不为一条异常数据整个崩掉。这个兜底刻意留在**本模块**（有测试），
    而不是写在 GUI 里 —— GUI 层（``app/web/``）整体 omit 于覆盖率，规则写在那儿等于没有护栏。
    """
    try:
        return color_for(EmotionLabel(label))
    except ValueError:
        return DIM_COLOR


def color_for_key_count(label: str, count: int) -> str:
    """``color_for_key`` + 「0 人置灰」的组合，供汇总面板按行取色。

    存在的理由是**别让 GUI 去拼规则**：汇总面板拿到的是 ``LABEL_ORDER`` 里的字符串
    键与一个整数，如果让它自己写 ``color_for_key(k) if count else DIM_COLOR``，
    那条「0 人置灰」的规则就跑到没有覆盖率的 GUI 层里去了。这里把它
    收回有测试的层，GUI 只调一个函数。

    ``color_for_count`` 是同一个规则的枚举入口（服务端/内部调用用），两者口径一致，
    ``test_colors.py`` 有交叉断言防止它们漂移。
    """
    return DIM_COLOR if is_dim(count) else color_for_key(label)


def fill_for(label: EmotionLabel) -> str:
    """取色标签的**填充色**。

    与 :func:`color_for` 同一套兜底：未登记的标签退回置灰色而不是抛错 ——
    展示层不该因为一条异常数据整个崩掉。
    """
    return STATE_FILLS.get(label, DIM_COLOR)


def fill_for_key(label: str) -> str:
    """按**线路格式的字符串键**取色标签填充色（与 :func:`color_for_key` 对称）。

    未登记的键退回置灰色而不是抛错，理由同 :func:`color_for_key`：这个兜底必须
    留在**有测试的层**，写在 GUI 层里等于没有护栏。
    """
    try:
        return fill_for(EmotionLabel(label))
    except ValueError:
        return DIM_COLOR


def _luminance(color: str) -> float:
    """WCAG 2.1 相对亮度（输入 ``#rrggbb``）。

    与 ``app/present/skin.py`` 的门槛用的是同一个公式 —— 那边是**断言**、
    这里是**运行时决策**，两处必须同源，否则「测试说达标、运行时却选了另一色」。
    """
    raw = color.lstrip("#")
    linear: list[float] = []
    for index in (0, 2, 4):
        srgb = int(raw[index : index + 2], 16) / 255.0
        linear.append(srgb / 12.92 if srgb <= 0.03928 else ((srgb + 0.055) / 1.055) ** 2.4)
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(first: float, second: float) -> float:
    """两个**相对亮度**之间的 WCAG 对比度。"""
    lighter, darker = max(first, second), min(first, second)
    return (lighter + 0.05) / (darker + 0.05)


def on_fill(fill: str) -> str:
    """给定填充色，返回**压在其上可读**的文字色（白或深）。

    为什么由函数算而不是逐状态手写一张表：手写表会在改 fill 时静默失配 ——
    改了底色忘了改前景，字就糊了，而**没有任何门禁会红**。这里按 WCAG 对比度
    现算，取白/深两候选中**对比度更高**的一个，规则与 ``test_contrast.py`` 同源。

    ⚠️ 判定必须实算两个对比度，**不能**用「填色亮度是否超过白/深两色亮度的中点」
    这类近似：对比度的交叉点不是亮度中点。实测本项目的绿 fill ``#2ea44f``
    （亮度 0.277）落在中点 0.509 之下，按中点法会选白字（3.21:1，不达标），
    而实算的正确解是深字（4.90:1）。交叉亮度实际在 0.218 附近 —— 两者差得很远。

    调用方仍须断言结果 ≥4.5:1（``test_contrast.py`` 已对四个 fill 逐一验过）：
    本函数只保证「两害相权取其轻」，不保证「一定达标」—— 若有人填一个中灰
    （如 ``#808080``），两个候选都只有 3.9 左右，那时该改的是 fill 不是这里。
    """
    fill_luminance = _luminance(fill)
    if _contrast(fill_luminance, _luminance(ON_LIGHT_TEXT)) >= _contrast(
        fill_luminance, _luminance(ON_DARK_TEXT)
    ):
        return ON_LIGHT_TEXT
    return ON_DARK_TEXT
