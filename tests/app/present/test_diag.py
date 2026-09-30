"""present.diag 的测试。

守什么：
1. **行结构恒定**：9 行、顺序不变（viewer / 在线 / 专注 / 困惑 / 分神 / 未知 /
   stale / payload / 刷新）。client 按位置逐行 render，顺序变了界面就乱。
2. **计数口径与 summary 一致**：只数 has_state（有 label）的格；hidden / 无结果 /
   未知标签不进任何档。
3. **畸形输入不抛错**：grid 缺失、cell 不是 Mapping、state 不是 Mapping——诊断
   是给人看的兜底视图，绝不能比主视图先崩。
4. **refresh_ms=None 显示 ``--``**：没测就是没测，不编数。
"""

from __future__ import annotations

import json
from typing import Any

from app.present.diag import diag_lines


def _payload(grid: list[dict[str, Any]]) -> dict[str, Any]:
    return {"viewer": "t01", "role": "teacher", "grid": grid}


def _cell(pid: str, label: str | None = None, *, stale: bool = False, hidden: bool = False):
    state = None
    if label is not None:
        state = {
            "label": label,
            "confidence": 0.8,
            "stale": stale,
            "timestamp": 100.0,
            "frame_id": 7,
        }
    return {"participant_id": pid, "role": "student", "hidden": hidden, "ts": 1.0, "state": state}


class TestStructure:
    def test_nine_lines_in_fixed_order(self):
        lines = diag_lines(_payload([]), viewer="t01", now_ts=1.0, refresh_ms=12.3)
        assert len(lines) == 9
        assert lines[0] == "viewer=t01"
        assert lines[1] == "在线 0"
        assert lines[2].startswith("专注")
        assert lines[3].startswith("困惑")
        assert lines[4].startswith("分神")
        assert lines[5].startswith("未知")
        assert lines[6].startswith("stale")
        assert lines[7].startswith("payload")
        assert lines[8].startswith("刷新")

    def test_viewer_echoed_verbatim(self):
        lines = diag_lines(_payload([]), viewer="s07", now_ts=0.0)
        assert lines[0] == "viewer=s07"


class TestCounting:
    def test_counts_each_label(self):
        grid = [
            _cell("s01", "focused"),
            _cell("s02", "focused", stale=True),
            _cell("s03", "confused"),
            _cell("s04", "distracted"),
            _cell("s05", "unknown"),
        ]
        lines = diag_lines(_payload(grid), viewer="t01", now_ts=0.0)
        assert lines[1] == "在线 5"
        assert lines[2] == "专注 2"
        assert lines[3] == "困惑 1"
        assert lines[4] == "分神 1"
        assert lines[5] == "未知 1"

    def test_stateless_and_hidden_cells_not_counted(self):
        grid = [
            _cell("s01", "focused"),
            _cell("s02"),  # 无结果
            _cell("s03", hidden=True),  # 隐藏（state 已被掩成 None）
            _cell("s04", "bogus-label"),  # 未知标签：不进任何档，也不抛错
        ]
        lines = diag_lines(_payload(grid), viewer="t01", now_ts=0.0)
        assert lines[1] == "在线 1"
        assert lines[2] == "专注 1"
        assert lines[5] == "未知 0"

    def test_stale_counted_separately(self):
        grid = [
            _cell("s01", "focused", stale=True),
            _cell("s02", "confused"),
            _cell("s03", "distracted", stale=True),
        ]
        lines = diag_lines(_payload(grid), viewer="t01", now_ts=0.0)
        assert lines[6] == "stale 2"


class TestRobustness:
    def test_grid_missing(self):
        lines = diag_lines({"viewer": "t01"}, viewer="t01", now_ts=0.0)
        assert lines[1] == "在线 0"
        assert lines[6] == "stale 0"

    def test_grid_not_a_list(self):
        lines = diag_lines({"grid": "oops"}, viewer="t01", now_ts=0.0)
        assert lines[1] == "在线 0"

    def test_cell_and_state_not_mappings(self):
        grid = ["not-a-mapping", {"participant_id": "s01", "state": "oops"}]
        lines = diag_lines(_payload(grid), viewer="t01", now_ts=0.0)
        assert lines[1] == "在线 0"
        assert lines[6] == "stale 0"


class TestPayloadBytes:
    def test_byte_count_matches_utf8_serialization(self):
        payload = _payload([_cell("s01", "focused")])
        lines = diag_lines(payload, viewer="t01", now_ts=0.0)
        expected = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        assert lines[7] == f"payload {expected} B"


class TestRefreshMs:
    def test_none_renders_dashes(self):
        lines = diag_lines(_payload([]), viewer="t01", now_ts=0.0, refresh_ms=None)
        assert lines[8] == "刷新 -- ms"

    def test_value_rounded_to_integer(self):
        lines = diag_lines(_payload([]), viewer="t01", now_ts=0.0, refresh_ms=12.7)
        assert lines[8] == "刷新 13 ms"
