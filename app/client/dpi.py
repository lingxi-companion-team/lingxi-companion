"""DPI 感知：让窗口按**原生分辨率**渲染，而不是被 Windows 位图拉伸。

问题（实测）
------------
Python 进程默认是 **DPI-unaware** 的。在 150% 缩放的屏幕上，Windows 会把整个窗口
按 1.5 倍做**位图放大** —— 文字与线条全部变糊，这是「字看起来发虚」的主因。

实测本机（2026-10-01）::

    GetProcessDpiAwareness = 0        # 0 = UNAWARE
    Tk 报屏幕               = 1440×960
    物理分辨率              = 2160×1440      # 恰好 1.5 倍 → 窗口被拉伸 1.5×
    winfo_fpixels('1i')     = 96.0           # Tk 被虚拟化后的 DPI，不是真实 144

解法
----
在 ``tk.Tk()`` **之前**声明 DPI 感知（``SetProcessDpiAwareness``）。之后 Tk 直接
按物理像素渲染，不再经过位图拉伸 —— 布局逐像素不变，只是**渲染分辨率变成原生**。

为什么还要锁 ``tk scaling``
---------------------------
声明感知后，Tk 会按真实 DPI 把「点」为单位的字号换算成更多像素（144 DPI 下
9pt → 18px，而不再是 12px）。但本项目的**几何常量是像素**（卡片 98×80、行高 38、
间距 8…），字号一涨就会溢出卡片。

所以 :func:`lock_tk_scaling` 把 ``tk scaling`` 固定成 96 DPI 的比值
（``96/72 ≈ 1.333``），让「字号 ↔ 像素几何」的比例**保持原样**。
净效果：**布局与改前完全一致，只是渲染得清晰**。

代价（要知情）：在 150% 缩放的屏幕上，窗口现在是 1440 **物理**像素（屏幕的 2/3），
而不是被拉伸到 2160 —— 所以肉眼看起来会比以前小一圈，但它是清晰的。
屏幕本身是 100% 缩放的机器（教室投影仪通常如此）**完全不受影响**。
"""

from __future__ import annotations

import ctypes
import sys
import tkinter as tk

__all__ = [
    "DPI_AWARE_PER_MONITOR",
    "DPI_AWARE_SYSTEM",
    "DPI_AWARE_UNAWARE",
    "BASE_TK_SCALING",
    "dpi_factor",
    "enable_dpi_awareness",
    "lock_tk_scaling",
]

#: ``SetProcessDpiAwareness`` 的三个档位（Windows 8.1+ 的 shcore）。
DPI_AWARE_UNAWARE = 0
DPI_AWARE_SYSTEM = 1
DPI_AWARE_PER_MONITOR = 2

#: Tk 在 96 DPI 下的 ``tk scaling`` 值（``96/72``）。项目所有像素几何都按它标定。
BASE_TK_SCALING = 96.0 / 72.0


def enable_dpi_awareness(mode: int = DPI_AWARE_SYSTEM) -> str:
    """声明本进程的 DPI 感知级别。**必须在 ``tk.Tk()`` 之前调用。**

    Returns:
        人类可读的结果描述（供 ``--selftest`` / 日志打印）。非 Windows 平台返回
        ``"non-windows (no-op)"`` —— 本函数在别的平台上是安全的空操作。
    """
    if sys.platform != "win32":
        return "non-windows (no-op)"
    # 优先 shcore（Win8.1+，可选 SYSTEM / PER_MONITOR）；老系统退回 user32 的布尔版。
    try:
        result = ctypes.windll.shcore.SetProcessDpiAwareness(mode)
        if result == 0:
            return f"shcore ok (mode={mode})"
        # 非 0 通常是「已经设置过」——不致命，继续尝试读回。
        return f"shcore returned {result} (可能已设置过)"
    except (AttributeError, OSError):
        pass
    try:
        ok = ctypes.windll.user32.SetProcessDPIAware()
        return f"user32 ok ({bool(ok)})"
    except (AttributeError, OSError) as exc:
        return f"failed: {type(exc).__name__}"


def lock_tk_scaling(root: tk.Misc, scaling: float = BASE_TK_SCALING) -> None:
    """把 ``tk scaling`` 固定成指定值（默认 96 DPI 的比值）。

    见模块 docstring：声明 DPI 感知后必须锁一次，否则字号会按真实 DPI 放大、
    与像素几何失配（文字溢出卡片）。

    在**创建任何控件之前**调用最稳妥 —— 已经建好的控件不会重算字号。
    """
    try:
        root.tk.call("tk", "scaling", float(scaling))
    except tk.TclError:
        # 个别 Tk 构建不允许写该变量；失败只影响字号缩放，不该让界面起不来。
        pass


def dpi_factor(root: tk.Misc, base: float = 96.0) -> float:
    """当前屏幕相对 ``base`` DPI 的缩放倍率（100% 缩放 → ``1.0``）。

    用真实 DPI 反推，而不是读注册表 —— 后者在「每显示器 DPI」下不可靠。
    取值失败时返回 ``1.0``（按不缩放处理）。
    """
    try:
        return float(root.winfo_fpixels("1i")) / base
    except (tk.TclError, TypeError, ValueError):
        return 1.0
