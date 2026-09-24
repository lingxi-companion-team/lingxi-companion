"""串行集成 CLI 的单测（C 主责）。

只断言**标准输出与退出码** —— 日志会被 ``logging`` 的全局 handler 持有流引用，
在 pytest 里跨用例并不可靠；而「CLI 该输出什么」本来就是 stdout 的契约。
"""

from __future__ import annotations

import pytest

from app.integration.__main__ import DEFAULT_FRAMES, main, parse_args


def test_parse_args_defaults():
    """默认值是「开箱可用」的那一组：mock 共识场景 + 学生身份。"""
    args = parse_args([])

    assert args.source == "mock"
    assert args.scenario == "consensus"
    assert args.frames == DEFAULT_FRAMES
    assert args.participant == "s01"
    assert args.role == "student"
    assert args.json is False
    assert args.serve is False


def test_main_runs_mock_scenario(capsys):
    """mock 路径跑得通，逐帧输出 + 末尾汇总。"""
    assert main(["--frames", "5", "--quiet"]) == 0
    out = capsys.readouterr().out

    assert "#0001 " in out
    assert "#0005 " in out
    assert "共 5 帧" in out
    assert "final=focused" in out


def test_main_rejects_unknown_scenario_with_exit_code_2(capsys):
    """未知场景名 → 退出码 2，并把可用场景名带到 stderr。"""
    assert main(["--scenario", "no_such_scenario"]) == 2
    err = capsys.readouterr().err

    assert "错误" in err
    assert "consensus" in err  # KeyError 自带可用名称列表


def test_main_prints_envelope_json(capsys):
    """``--json`` 额外打出前端信封（含 state）。"""
    assert main(["--frames", "4", "--json", "--quiet"]) == 0
    out = capsys.readouterr().out

    assert '"participant_id": "s01"' in out
    assert '"role": "student"' in out
    assert '"state": null' not in out  # 共识场景第 4 帧已确认


def test_main_teacher_publishes_no_state(capsys):
    """教师身份下信封里的 state 恒为 null（A1：教师不占格）。"""
    assert main(["--json", "--role", "teacher", "--frames", "4", "--quiet"]) == 0
    out = capsys.readouterr().out

    assert '"role": "teacher"' in out
    assert '"state": null' in out


def test_main_synthetic_source_runs_the_full_chain(capsys):
    """synthetic 路径跑满三路，环境路给出真实 E。"""
    assert main(["--source", "synthetic", "--frames", "3", "--quiet"]) == 0
    out = capsys.readouterr().out

    assert "#0000 " in out  # 合成源从 0 起
    assert "env=E" in out
    assert "共 3 帧" in out


def test_main_conflict_scenario_never_confirms(capsys):
    """冲突场景全程拒判，CLI 也应如实显示 unknown。"""
    assert main(["--scenario", "conflict", "--frames", "4", "--quiet"]) == 0
    out = capsys.readouterr().out

    assert "(conflict)" in out
    assert "确认状态 0 帧" in out
    assert "最终 -" in out


def test_main_rejects_illegal_texture():
    """非法纹理由 argparse 直接拒绝（退出码 2）。"""
    with pytest.raises(SystemExit) as excinfo:
        main(["--source", "synthetic", "--texture", "blur"])

    assert excinfo.value.code == 2
