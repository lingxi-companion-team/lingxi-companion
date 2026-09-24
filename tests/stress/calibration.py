"""模糊标定：复现 ``agents/env/agent.py::_LAP_VAR_REF`` 的标定表。

为什么需要一个可执行的标定脚本
------------------------------
``_LAP_VAR_REF``（参考拉普拉斯方差）是环境智能体里唯一一个**经验常数**：
它决定「多糊才算糊」。这类常数最怕两件事 ——

1. **来路不明**：若干年前在某人机器上试出来一个数，后人不敢改；
2. **与应用脱节**：采样方式 / 网格尺寸改了，常数却没跟着变，而单测只检查
   单调性，不会发现基准已经漂了。

所以把它做成脚本 + 报告（``docs/reports/``）：换摄像头、换光照后跑一遍，
就能拿到新的参考值，并知道该改哪一行。

⚠️ 标定必须在**图像域**做模糊，不能在采样网格上做
--------------------------------------------------
失焦是光学现象，发生在降采样**之前**。若先降到 32×32 网格再模糊，等于在
「已经丢掉高频」的信号上再丢一次，衰减曲线会明显偏慢，由此标出的参考值会偏小。
本脚本因此把噪声图模糊完、再交给 :func:`sample_luma` 采样。

用法（**在仓库根目录**执行）::

    python -m tests.stress.calibration
    python -m tests.stress.calibration --side 64 --passes 0,1,2,3,5,8
"""

from __future__ import annotations

import argparse

from agents.env.agent import (
    _BLUR_AT_REFERENCE,
    _LAP_VAR_REF,
    SAMPLE_COLS,
    SAMPLE_ROWS,
    evaluate_luma,
    laplacian_variance,
    sample_luma,
)
from tests.stress.stresskit import SyntheticFrame

#: 默认模糊遍数梯度。前几遍衰减最快，所以取对数式的稀疏间隔。
DEFAULT_PASSES = (0, 1, 2, 3, 5, 8)

#: 与 ``common.input_spec`` 一致的 dtype 替身。
_DTYPE = type("_DType", (), {"name": "uint8"})()


class _MatrixFrame:
    """由**显式**像素矩阵支撑的帧替身。

    用途：先在图像域把噪声图模糊掉，再让 :func:`sample_luma` 采样。
    ``SyntheticFrame`` 的像素是按需算的，表达不了「已经模糊过」的图。
    """

    def __init__(self, luma: list[list[float]]) -> None:
        self._rows = [[(int(value),) * 3 for value in row] for row in luma]
        height = len(self._rows)
        width = len(self._rows[0]) if height else 0
        self.shape = (height, width, 3)
        self.dtype = _DTYPE

    def __getitem__(self, y: int) -> list[tuple[int, int, int]]:
        return self._rows[y]


def box_blur(luma: list[list[float]], *, passes: int = 1) -> list[list[float]]:
    """在图像域连续施加 ``passes`` 遍 3×3 盒式模糊（边界按可用邻居求均值）。"""
    side_y = len(luma)
    side_x = len(luma[0]) if side_y else 0
    current = [row[:] for row in luma]

    for _ in range(passes):
        blurred = [[0.0] * side_x for _ in range(side_y)]
        for y in range(side_y):
            for x in range(side_x):
                total = 0.0
                count = 0
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        ny, nx = y + dy, x + dx
                        if 0 <= ny < side_y and 0 <= nx < side_x:
                            total += current[ny][nx]
                            count += 1
                blurred[y][x] = total / count
        current = blurred
    return current


def reference_grid(*, frame_side: int = 64, passes: int = 0, seed: int = 0) -> list[list[float]]:
    """取噪声图 →（可选）图像域模糊 → 降采样，得到 ``_LAP_VAR_REF`` 所指的网格。

    ⚠️ 网格尺寸必须与 :data:`SAMPLE_ROWS` / :data:`SAMPLE_COLS` 一致：
    拉普拉斯是二阶差分，网格越密、相邻像素差越大，方差就越大。
    换了网格尺寸却不重标，常数会静默失准。
    """
    noise = SyntheticFrame(height=frame_side, width=frame_side, texture="noise", seed=seed)
    luma = [[float(noise[y][x][0]) for x in range(frame_side)] for y in range(frame_side)]
    if passes:
        luma = box_blur(luma, passes=passes)
    return sample_luma(_MatrixFrame(luma), cols=SAMPLE_COLS, rows=SAMPLE_ROWS)


def main() -> None:
    parser = argparse.ArgumentParser(description="环境智能体模糊标定")
    parser.add_argument("--side", type=int, default=64, help="合成噪声图的边长（像素）")
    parser.add_argument(
        "--passes",
        type=str,
        default=",".join(str(item) for item in DEFAULT_PASSES),
        help="模糊遍数梯度，逗号分隔",
    )
    parser.add_argument("--anchor", type=int, default=2, help="锚点遍数（设计上应落在容忍线附近）")
    args = parser.parse_args()

    passes = [int(item) for item in args.passes.split(",") if item.strip()]

    print("## 标定条件")
    print()
    print(f"- 网格：{SAMPLE_ROWS}×{SAMPLE_COLS}（与 `agents.env.agent.SAMPLE_ROWS/COLS` 一致）")
    print(f"- 基准图：{args.side}×{args.side} 合成噪声（确定性，seed=0），**在图像域模糊**")
    print(f"- 当前 `_LAP_VAR_REF`：{_LAP_VAR_REF}")
    print(f"- `_BLUR_AT_REFERENCE`：{_BLUR_AT_REFERENCE}（参考方差处的 blur 取值）")
    print()

    print("| 模糊遍数 | 拉普拉斯方差 | 归一化 x = lap/ref | blur | E（默认阈值） |")
    print("|--:|--:|--:|--:|--:|")

    rows: list[tuple[int, float, float, float, float]] = []
    for count in passes:
        grid = reference_grid(frame_side=args.side, passes=count)
        lap = laplacian_variance(grid)
        metrics = evaluate_luma(grid)
        ratio = lap / _LAP_VAR_REF
        rows.append((count, lap, ratio, metrics.blur, metrics.env_score))
        print(
            f"| {count} | {lap:.0f} | {ratio:.3f} | {metrics.blur:.3f} | {metrics.env_score:.3f} |"
        )
    print()

    print("## 与容忍线的关系")
    print()
    print(f"blur 等于 `blur_tolerance`（默认 0.4）当且仅当 x = 1，即拉普拉斯方差 = {_LAP_VAR_REF}")
    crossing: int | None = None
    for count, lap, ratio, blur, _score in rows:
        if ratio >= 1.0:
            print(
                f"- {count} 遍：lap = {lap:.0f}、x = {ratio:.3f} → 仍高于容忍线（blur {blur:.3f}）"
            )
            continue
        print(
            f"- {count} 遍：lap = {lap:.0f}、x = {ratio:.3f} → **已越过容忍线**（blur {blur:.3f}）"
        )
        crossing = count
        break
    print()

    anchor_row = next((row for row in rows if row[0] == args.anchor), None)
    if anchor_row is None:
        print(f"> 梯度里没有 {args.anchor} 遍，无法核对锚点；请把它加进 `--passes`。")
    else:
        _count, anchor_lap, anchor_ratio, anchor_blur, _score = anchor_row
        print(f"> 锚点是「{args.anchor} 遍盒式模糊 ≈ 容忍线」（见 `_LAP_VAR_REF` 的注释）。")
        print(
            f"> 实测 {args.anchor} 遍：lap = {anchor_lap:.0f}、x = {anchor_ratio:.3f}、"
            f"blur = {anchor_blur:.3f}"
        )
        print(
            f"> 要让锚点**正好**落在容忍线上，参考值应取 **{anchor_lap:.0f}**"
            f"（当前 {_LAP_VAR_REF:.0f}），即把 `_LAP_VAR_REF` 改成该值。"
        )
    if crossing is not None:
        print(f"> 实测「首次越过容忍线」发生在 **{crossing} 遍**盒式模糊。")


if __name__ == "__main__":
    main()
