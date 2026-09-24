"""压测基准脚本：打印可粘贴的 Markdown 表格，供 ``docs/reports/`` 的报告取数。

用法（**在仓库根目录**执行）::

    python -m tests.stress.benchmark
    python -m tests.stress.benchmark --iterations 2000

它不做断言（断言在 ``tests/stress/test_*.py`` 里），只负责**测量与呈现**：
压测报告里的每个数字都应当能用这条命令现场复现，否则报告就成了无源之谈。

⚠️ 输出里只包含机器与解释器信息，**不含任何机器名 / 用户名** —— 报告是要入库的
公开文件。
"""

from __future__ import annotations

import argparse
import os
import platform

from tests.stress.stresskit import Timing, live_growth_bytes, measure, repeat, tail_growth_ratio
from tests.stress.workloads import (
    env_agent_step,
    fusion_step,
    sample_only_step,
    serial_chain_step,
)

#: 环境侧的分辨率梯度。用来证明「耗时与像素数无关」——只与采样点数有关。
RESOLUTIONS = ((240, 320), (480, 640), (720, 1280), (1080, 1920))

#: 长跑迭代次数的默认值。检查单次成本是否随调用次数增长（无状态性）。
#:
#: ⚠️ 这个数不能太大：``tracemalloc`` 会让每次分配慢一个数量级，长跑部分
#: 往往是整条基准里最慢的，调大前先确认总时长仍在可接受范围。
DEFAULT_LONG_RUN = 5000


def _row(label: str, timing: Timing, extra: str = "") -> str:
    """一行 Markdown 表格。"""
    return (
        f"| {label} | {timing.median_ms:.4f} | {timing.p95_ms:.4f} | "
        f"{timing.best_ms:.4f} | {timing.iterations} | {extra} |"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="灵犀学伴压测基准")
    parser.add_argument("--iterations", type=int, default=300, help="环境侧单帧测量次数")
    parser.add_argument("--chain-iterations", type=int, default=1000, help="链路单帧测量次数")
    parser.add_argument(
        "--long-run", type=int, default=DEFAULT_LONG_RUN, help="长跑稳定性检查的迭代次数"
    )
    args = parser.parse_args()

    print("## 运行环境")
    print()
    print(f"- Python：{platform.python_version()}")
    print(f"- 平台：{platform.system()} {platform.machine()}")
    print(f"- CPU 逻辑核数：{os.cpu_count()}")
    print()

    header = "| 载荷 | 中位 (ms) | p95 (ms) | 最快 (ms) | 次数 | 备注 |"
    divider = "|:--|--:|--:|--:|--:|:--|"

    print("## 环境智能体：单帧评估 vs 分辨率")
    print()
    print(header)
    print(divider)
    for height, width in RESOLUTIONS:
        step = env_agent_step(height=height, width=width)
        timing = measure(step, iterations=args.iterations)
        print(_row(f"{width}×{height}（噪声帧）", timing, "按需合成帧"))
    for texture in ("flat", "smooth"):
        step = env_agent_step(texture=texture)
        timing = measure(step, iterations=args.iterations)
        print(_row(f"640×480（{texture} 帧）", timing, "无细节路径"))
    print()

    print("## 成本分解（把「读像素」与「算指标」分开）")
    print()
    print("> 合成帧的像素是纯 Python 算出来的，真帧是 numpy 数组、访问快两个数量级。")
    print("> 因此绝对值会随载体变化，但**两者之差**（度量与合成）才是算法自身的成本。")
    print()
    print(header)
    print(divider)
    sample_timing = measure(sample_only_step(), iterations=args.iterations)
    assess_timing = measure(env_agent_step(), iterations=args.iterations)
    print(_row("采样（1024 个块中心像素）", sample_timing, "载体成本"))
    print(_row("评估一帧（采样 + 度量 + 合成）", assess_timing, ""))
    print(
        f"| 其中：度量与合成 | {assess_timing.median_ms - sample_timing.median_ms:.4f} | "
        f"— | — | — | 两者相减 |"
    )
    print()

    print("## 融合层与串行链路")
    print()
    print(header)
    print(divider)
    fusion_timing = measure(fusion_step(), iterations=args.chain_iterations)
    print(_row("融合单帧（mock 共识场景）", fusion_timing, ""))
    chain_timing = measure(serial_chain_step(), iterations=args.chain_iterations)
    print(_row("串行链路单帧（环境真实 + 两路假）", chain_timing, "含平滑"))
    print()

    print(f"链路折算吞吐：{1000.0 / chain_timing.median_ms:.0f} 帧/秒")
    print()

    long_run = args.long_run
    print(f"## 长跑稳定性（{long_run} 次）")
    print()
    print(header)
    print(divider)
    long_step = serial_chain_step()
    long_timing = measure(long_step, iterations=long_run, warmup=10)
    print(
        _row(
            "串行链路",
            long_timing,
            f"尾/首窗口中位比 {tail_growth_ratio(long_timing):.2f}",
        )
    )
    env_step = env_agent_step()
    env_timing = measure(env_step, iterations=long_run, warmup=10)
    print(
        _row(
            "环境智能体",
            env_timing,
            f"尾/首窗口中位比 {tail_growth_ratio(env_timing):.2f}",
        )
    )
    print()

    growth = live_growth_bytes(repeat(serial_chain_step(), long_run))
    per_frame = growth / long_run
    print(
        f"长跑存活内存净增长：{growth / 1024:.1f} KB（{long_run} 帧，约 {per_frame:.2f} 字节/帧）"
    )
    print()
    print("> 单帧成本若随调用次数增长，或存活内存按帧数线性增长，说明某处状态未截断。")


if __name__ == "__main__":
    main()
