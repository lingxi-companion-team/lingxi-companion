"""三栏布局的宽度分配（纯逻辑，无 GUI 依赖）。

v7 仪表盘是**三栏**：左导航 | 中画布 | 右配置面板。三栏在 1440 宽下都很舒服，
但教室投影常见 1024 / 1280，硬排会挤爆中栏。所以「宽度不够时**先收哪一栏、
后收哪一栏**」是一条需要被测试钉死的**产品决策**，放 present 层。

收缩顺序（设计稿 §七 风险表「三栏在小屏放不下」）
------------------------------------------------
**先收右面板，再收左导航** —— 依据是信息优先级：

1. 中栏（KPI + 图表 + 表格）是**主体**，永远保留；
2. 右面板是**配置/筛选**，属于「想改的时候才用」，先收为窄栏、再整栏隐藏；
3. 左导航是**框架**，收了会「不知道自己在哪」，所以它最后才从文字条变图标条、
   再隐藏（图标条仍能表达「当前在第几项」）。

这条顺序不是拍脑袋：反过来（先收导航）会让主界面在小屏上失去导航锚点，
而右面板本就经常是收起的。

四档（阈值从大到小，取第一个 ``min_width <= 宽度`` 的档）
--------------------------------------------------------
============  ============  ==================
宽度下限       左导航         右面板
============  ============  ==================
1280          完整 208px     完整 320px
1120          完整 208px     窄 248px
960           图标条 72px    窄 248px
800           图标条 72px    隐藏 0
（更窄）       隐藏 0         隐藏 0
============  ============  ==================
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "INSPECTOR_FULL",
    "INSPECTOR_HIDDEN",
    "INSPECTOR_NARROW",
    "NAV_FULL",
    "NAV_HIDDEN",
    "NAV_ICONS",
    "Layout",
    "layout_for_width",
]

#: 左导航三态。
NAV_FULL = "full"
NAV_ICONS = "icons"
NAV_HIDDEN = "hidden"

#: 右面板三态。
INSPECTOR_FULL = "full"
INSPECTOR_NARROW = "narrow"
INSPECTOR_HIDDEN = "hidden"

#: 各档宽度（像素）。
_NAV_WIDTHS = {NAV_FULL: 208, NAV_ICONS: 72, NAV_HIDDEN: 0}
_INSPECTOR_WIDTHS = {INSPECTOR_FULL: 320, INSPECTOR_NARROW: 248, INSPECTOR_HIDDEN: 0}

#: 中栏的最小可用宽度。低于它就不该再让「三栏」这个形态成立 —— 但本模块只负责
#: 按阈值选档，不做二次挤压（阈值表已保证各档下中栏够宽）。
MIN_MAIN_WIDTH = 560

#: 档位表：(宽度下限, 左导航模式, 右面板模式)，**从宽到窄**。
#: 取值顺序即「先收右面板、再收左导航」这条决策的编码。
_BANDS: tuple[tuple[int, str, str], ...] = (
    (1280, NAV_FULL, INSPECTOR_FULL),
    (1120, NAV_FULL, INSPECTOR_NARROW),
    (960, NAV_ICONS, INSPECTOR_NARROW),
    (800, NAV_ICONS, INSPECTOR_HIDDEN),
    (0, NAV_HIDDEN, INSPECTOR_HIDDEN),
)


@dataclass(frozen=True)
class Layout:
    """一档三栏布局的宽度分配。client 直接按它摆放三栏。

    ``main_width`` 是**剩余宽度**（总宽 - 左导航 - 右面板），可能为 0（极窄窗口）。
    """

    nav_width: int
    main_width: int
    inspector_width: int
    nav_mode: str
    inspector_mode: str

    @property
    def nav_visible(self) -> bool:
        """左导航是否可见。"""
        return self.nav_mode != NAV_HIDDEN

    @property
    def inspector_visible(self) -> bool:
        """右面板是否可见。"""
        return self.inspector_mode != INSPECTOR_HIDDEN


def layout_for_width(width_px: float) -> Layout:
    """按窗口宽度返回三栏布局。

    边界语义：恰好等于阈值归**更宽**那一档（``>=`` 语义），与
    :func:`app.present.scale.scale_for_width` 的 ``<`` 风格**相反** —— 这里用
    ``>=`` 是因为档位表的 ``min_width`` 读作「至少这么宽才配得上这一档」，
    比「小于阈值就降档」更直观。

    非法输入（非数值 / 负值）按最保守档处理（左右都隐藏，把宽度全给中栏），
    不抛错 —— 与展示层其它入口的兜底约定一致。
    """
    try:
        width = float(width_px)
    except (TypeError, ValueError):
        width = 0.0
    if width < 0:
        width = 0.0

    nav_mode, inspector_mode = _BANDS[-1][1], _BANDS[-1][2]
    for min_width, nav, inspector in _BANDS:
        if width >= min_width:
            nav_mode, inspector_mode = nav, inspector
            break

    nav_width = _NAV_WIDTHS[nav_mode]
    inspector_width = _INSPECTOR_WIDTHS[inspector_mode]
    main_width = max(0, int(width) - nav_width - inspector_width)
    return Layout(
        nav_width=nav_width,
        main_width=main_width,
        inspector_width=inspector_width,
        nav_mode=nav_mode,
        inspector_mode=inspector_mode,
    )
