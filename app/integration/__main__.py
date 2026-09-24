"""串行集成主流程的命令行入口（C 主责）。

用法::

    python -m app.integration                                  # mock「共识」场景，12 帧
    python -m app.integration --source synthetic --frames 30   # 全链路 + 合成帧
    python -m app.integration --scenario conflict --frames 6   # 看冲突识别如何拒判
    python -m app.integration --scenario low_light --json      # 额外打出前端信封
    python -m app.integration --serve --port 8765              # 起 Hub 只读端点

只依赖标准库，因此在任何机器上都能跑 —— 这正是任务计划 §四 风险应对里
「先保证 CLI 端到端可运行」要的那条底线：**任何一环没交付，CLI 仍能跑通**。

⚠️ 表情 / 行为两路目前用 ``common.mock`` 的假智能体顶替（真模型未交付），
启动时会**明文告警**。因此本命令的输出只证明「链路装配正确 + 环境路真实计算」，
**不能**用来声称融合精度。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Sequence
from typing import TYPE_CHECKING

from app.integration.session import LOGGER_NAME, FrameReport, SerialSession
from app.integration.sources import (
    TEXTURES,
    FrameSource,
    MockScenarioSource,
    PerceptionSource,
    SyntheticFrameSource,
)
from common.mock import generator
from common.perception_types import AGENT_BEHAVIOR, AGENT_EXPRESSION, EmotionLabel

if TYPE_CHECKING:  # pragma: no cover
    from app.hub import Hub

__all__ = ["DEFAULT_FRAMES", "build_session", "main", "parse_args"]

#: 默认帧数。取 12 是有理由的：mock 场景多为 4 帧，12 正好让「3 中 2」窗口
#: 填满两轮以上，能同时看到「首次确认」与「确认后维持」两种输出。
DEFAULT_FRAMES = 12


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """解析命令行参数。"""
    scenarios = ", ".join(sorted(item.name for item in generator.SCENARIOS))
    parser = argparse.ArgumentParser(
        prog="python -m app.integration",
        description="串行集成主流程：输入源 → 三路智能体 → 融合 → 平滑 → 前端信封",
    )
    parser.add_argument(
        "--source",
        choices=("mock", "synthetic"),
        default="mock",
        help="mock=回放 common/mock 感知流（跳过三路）；synthetic=合成帧跑全链路",
    )
    parser.add_argument(
        "--scenario", default="consensus", help=f"--source mock 的场景名：{scenarios}"
    )
    parser.add_argument(
        "--frames",
        type=int,
        default=DEFAULT_FRAMES,
        help=f"最多执行帧数（默认 {DEFAULT_FRAMES}）",
    )
    parser.add_argument(
        "--texture",
        choices=TEXTURES,
        default="noise",
        help="--source synthetic 的纹理（noise 高频 / flat 无细节 / smooth 渐变）",
    )
    parser.add_argument("--fps", type=float, default=10.0, help="--source synthetic 的时间戳帧率")
    parser.add_argument("--participant", default="s01", help="前端信封里的参与者标识")
    parser.add_argument(
        "--role",
        choices=("student", "teacher"),
        default="student",
        help="student 输出自己的状态；teacher 不占格（见 app/envelope.py）",
    )
    parser.add_argument("--json", action="store_true", help="末尾额外打印前端信封 JSON")
    parser.add_argument("--serve", action="store_true", help="跑完后启动只读 HTTP 端点（阻塞）")
    parser.add_argument("--host", default="127.0.0.1", help="--serve 的绑定地址")
    parser.add_argument("--port", type=int, default=8765, help="--serve 的端口")
    parser.add_argument("--quiet", action="store_true", help="抑制信息级日志（告警仍会输出）")
    return parser.parse_args(argv)


def build_session(args: argparse.Namespace, hub: Hub | None = None) -> SerialSession:
    """按参数装配会话。

    感知两路用假智能体顶替并**显式告警** —— 静默替代是最坏的选项：演示时
    没人分得清屏幕上跳动的标签来自真模型还是常量。
    """
    logging.getLogger(LOGGER_NAME).warning(
        "表情 / 行为两路使用 common.mock 假智能体（真模型未交付）→ "
        "本结果只验证链路装配，**不代表融合精度**"
    )
    return SerialSession(
        expression=generator.fake_agent(AGENT_EXPRESSION, EmotionLabel.FOCUSED, 0.85),
        behavior=generator.fake_agent(AGENT_BEHAVIOR, EmotionLabel.FOCUSED, 0.80),
        participant_id=args.participant,
        role=args.role,
        hub=hub,
    )


def _make_source(args: argparse.Namespace) -> PerceptionSource | FrameSource:
    """按参数造输入源。

    ``--source mock`` 时把场景**重复到** ``--frames`` 帧：mock 场景只有 4~5 帧，
    跑 12 帧要看两轮以上。重复次数在这里算，是为了让 ``--frames`` 成为唯一
    需要用户关心的量。场景名非法时 ``get_scenario`` 抛 ``KeyError``（含可用名称），
    由 :func:`main` 转成退出码 2。
    """
    if args.source == "synthetic":
        return SyntheticFrameSource(frames=args.frames, texture=args.texture, fps=args.fps)

    per_round = max(1, len(generator.get_scenario(args.scenario).frames))
    repeat = max(1, -(-args.frames // per_round))  # 向上取整：帧数不足一轮也要跑一轮
    return MockScenarioSource(args.scenario, repeat=repeat)


def _serve(hub: Hub, host: str, port: int) -> None:  # pragma: no cover
    """启动只读端点（阻塞）。真实运行时使用，CI 里不可能跑到。"""
    from app.hub import serve

    logging.getLogger(LOGGER_NAME).info("只读端点：http://%s:%d/snapshot?viewer=...", host, port)
    serve(hub, host, port)


def main(argv: Sequence[str] | None = None) -> int:
    """命令行主入口。``0`` 正常，``2`` 参数或场景名有误。"""
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    hub: Hub | None = None
    if args.serve:  # pragma: no cover
        from app.hub import Hub as _Hub

        hub = _Hub()

    try:
        source = _make_source(args)
    except KeyError as exc:
        # get_scenario 的 KeyError 已带可用场景名列表，原样透出即可
        print(f"错误：{exc}", file=sys.stderr)
        return 2

    session = build_session(args, hub)
    reports: list[FrameReport] = []
    participant = None
    try:
        for report in session.iter_reports(source, max_frames=args.frames):
            print(report.line())
            participant = session.publish(report)
            reports.append(report)
    finally:
        session.close()

    confirmed = sum(1 for item in reports if item.final is not None and not item.final.stale)
    degraded = sum(1 for item in reports if item.degraded)
    last = participant.state.label.value if participant is not None and participant.state else "-"
    print(f"—— 共 {len(reports)} 帧；确认状态 {confirmed} 帧；降级 {degraded} 帧；最终 {last}")
    if args.json and participant is not None:
        print(json.dumps(participant.to_dict(), ensure_ascii=False, indent=2))
    if args.serve:  # pragma: no cover
        assert hub is not None
        _serve(hub, args.host, args.port)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
