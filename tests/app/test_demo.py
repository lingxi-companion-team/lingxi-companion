"""``app.demo`` 的测试 —— 演示环境本身也是代码，也有会坏的地方。

这里守的三件事都是**实测踩过的坑**，不是假想的：

1. **「未公开」开关会被喂数线程冲掉**（实测 0.9 秒后自己弹回去）；
2. **帧时间戳是合成序号而不是墙钟**，导致前端每一格都显示「维持中」；
3. **预热不足**时打开页面看到的是一屏「无结果」的空宫格。

第 3 条由 ``DemoDriver.WARMUP_ROUNDS`` 与 ``start()`` 保证，前两条各有一条测试。
"""

from __future__ import annotations

import time

from app.demo import (
    DEFAULT_TEACHER_ID,
    DemoDriver,
    build_demo_hub,
    default_demo_room,
    preset_hidden,
)
from app.hub import Hub
from app.room import POLICY_OPTIONAL


def _fresh(students: int = 4) -> tuple[Hub, DemoDriver]:
    hub, driver = build_demo_hub(students)
    driver.start()
    return hub, driver


def test_default_room_is_optional_and_named() -> None:
    """演示房间默认走「个人自主选择」—— 那是需求文档 §模块三.2 的默认档。"""
    room = default_demo_room()
    assert room.publish_policy == POLICY_OPTIONAL
    assert room.name and room.topic and room.invite_code


def test_start_warms_up_before_returning() -> None:
    """预热：``start()`` 返回时，宫格里就该已经有真结果了。

    平滑层是「3 中 2」的，第 1、2 帧窗口没填满、``final`` 必然是 ``None``。
    若只跑一帧就交界面，用户打开页面看到的是一屏「无结果」—— 那不是 bug，
    是**预热不足**。这条把「start 之后立即能看」钉住。
    """
    hub, driver = _fresh()
    try:
        payload = hub.snapshot_for("s01")
        assert len(payload["grid"]) > 0
        assert payload["summary"]["online_count"] > 0
    finally:
        driver.stop()


def test_the_visibility_toggle_survives_a_driver_tick() -> None:
    """**回归测试**：``POST /hidden`` 写下的开关必须扛过下一帧。

    没有 ``DemoDriver.pump`` 里那次「从 hub 回读再让会话采纳」，这个开关会在
    0.9 秒后被 ``publish`` 用会话自己的 ``hidden=False`` 冲掉 —— 界面上表现为
    「开关自己弹回去了」，而原因在另一个线程里，极难查。
    """
    hub, driver = _fresh()
    try:
        hub.set_hidden("s01", True)
        assert hub.get("s01").hidden is True
        driver.pump()
        assert hub.get("s01").hidden is True, "喂数线程把开关冲掉了"
        driver.pump()
        assert hub.get("s01").hidden is True
    finally:
        driver.stop()


def test_a_toggle_back_to_published_also_survives() -> None:
    """反向也要扛住 —— 否则「重新公开」是个一次性动作。"""
    hub, driver = _fresh()
    try:
        hub.set_hidden("s01", True)
        driver.pump()
        hub.set_hidden("s01", False)
        driver.pump()
        assert hub.get("s01").hidden is False
    finally:
        driver.stop()


def test_frames_carry_a_wall_clock_timestamp() -> None:
    """**回归测试**：帧时间戳必须是墙钟，不是 mock 场景里的合成序号。

    mock 场景自带的 ``env.ts`` 是 ``0.1 / 0.2 / 0.3``（给单测用的确定性序列），
    而前端的新鲜度判定是 ``now - state.timestamp`` —— 拿墙钟去减 ``0.3``
    会得到十七亿秒，于是**每一格都显示「维持中」**，四档新鲜度等于废掉。
    """
    hub, driver = _fresh()
    try:
        before = time.time()
        payload = hub.snapshot_for("s01")
        me = payload["me"]
        assert me is not None and me["state"] is not None
        assert abs(me["state"]["timestamp"] - before) < 60.0
    finally:
        driver.stop()


def test_the_teacher_is_registered_but_takes_no_cell() -> None:
    """发起人率先登记且**不带状态** —— 它因此天然不占格，无需任何前端判断。"""
    hub, driver = _fresh()
    try:
        assert hub.get(DEFAULT_TEACHER_ID) is not None
        payload = hub.snapshot_for(DEFAULT_TEACHER_ID)
        assert payload["initiator"] is True
        assert all(cell["participant_id"] != DEFAULT_TEACHER_ID for cell in payload["grid"])
    finally:
        driver.stop()


# ── preset_hidden ───────────────────────────────────────────────────────


def test_preset_hidden_marks_the_named_people() -> None:
    hub, driver = _fresh(6)
    try:
        assert preset_hidden(hub, ("s03", "s05")) == 2  # type: ignore[arg-type]
        assert hub.get("s03").hidden is True
        assert hub.get("s05").hidden is True
        assert hub.get("s01").hidden is False
    finally:
        driver.stop()


def test_preset_hidden_skips_unknown_ids_instead_of_raising() -> None:
    """演示里常会点名一个不存在的编号（比如 ``--students 3`` 却点了 ``s17``）。

    这条不是「顺手容错」：真实场景是「演示配置写死了几个编号，而人数是可配的」，
    两者对不上时**该静默跳过**，不该让整个演示起不来。
    """
    hub, driver = _fresh(3)
    try:
        assert preset_hidden(hub, ("s03", "s99")) == 1  # type: ignore[arg-type]
    finally:
        driver.stop()


def test_preset_hidden_sticks_after_a_tick() -> None:
    """预设也要扛过喂数线程 —— 与界面上的开关走同一条回读路径。"""
    hub, driver = _fresh(6)
    try:
        preset_hidden(hub, ("s03",))  # type: ignore[arg-type]
        driver.pump()
        assert hub.get("s03").hidden is True
    finally:
        driver.stop()


def test_preset_hidden_does_not_touch_perception_results() -> None:
    """改的是**演示配置**（谁选择不公开），不是感知结果。

    这条守住「不填假数据」的边界：预设之后，各人的状态仍然是真的过了一遍融合与平滑。
    """
    hub, driver = _fresh(6)
    try:
        before = {p.participant_id: p.state for p in hub.participants if p.state is not None}
        preset_hidden(hub, ("s03",))  # type: ignore[arg-type]
        after = {p.participant_id: p.state for p in hub.participants if p.state is not None}
        assert before == after
    finally:
        driver.stop()
