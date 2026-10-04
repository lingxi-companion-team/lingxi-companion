"""``app.present.console`` 的守卫。

这一套测试要挡的是**一类特定的谎**：把「没做的」画成「能用的」。
管理台的清单里大部分条目后端还没接通，而它们看起来与已接通的条目长得一模一样 ——
一旦有人顺手把 ``KIND_UNWIRED`` 改成 ``KIND_WIRED``，界面上就会多一个点了没反应的控件，
而**没有任何别的测试会红**。

所以这里逐条钉住：
- 每种 kind 都真的存在（防止「全都标成 wired」这种退化）；
- 未接入条目必须引用需求文档章节（不指来源的「未接入」等于推诿）；
- ``fact`` 条目里的话**是可验证的断言**，不是宣传语 —— 见
  :func:`test_the_no_backdoor_claim_is_actually_enforced_by_the_server`。
"""

from __future__ import annotations

import pytest

from app.envelope import ParticipantState
from app.present.console import (
    CONSOLE_SECTIONS,
    KIND_FACT,
    KIND_READONLY,
    KIND_TEXT,
    KIND_UNWIRED,
    KIND_WIRED,
    KINDS,
    ConsoleItem,
    ConsoleSection,
    console_items,
    item,
    unwired_items,
    visible_sections,
)
from app.present.visibility import visible_to
from app.room import POLICY_MANDATORY, RoomInfo
from common.perception_types import EmotionLabel, FinalState


def _student(pid: str, *, hidden: bool = False) -> ParticipantState:
    state = FinalState(label=EmotionLabel.FOCUSED, timestamp=1.0, stale=False, confidence=0.9)
    return ParticipantState(pid, state=state, hidden=hidden)


def _teacher(pid: str = "t01") -> ParticipantState:
    return ParticipantState(pid, role="teacher")


# ── 结构完整性 ──────────────────────────────────────────────────────────


def test_every_section_has_a_unique_key() -> None:
    keys = [section.key for section in CONSOLE_SECTIONS]
    assert len(keys) == len(set(keys))


def test_every_item_has_a_unique_key() -> None:
    keys = [entry.key for entry in console_items()]
    assert len(keys) == len(set(keys))


def test_section_keys_are_not_reused_as_item_keys() -> None:
    """分区键与条目键同处一个命名空间会让「按 key 找东西」变得含糊。"""
    assert not {section.key for section in CONSOLE_SECTIONS} & {e.key for e in console_items()}


def test_all_four_kinds_are_actually_used() -> None:
    """四种 kind 都必须有实例。

    这条挡的是退化：若有人把所有未接入项都标成 ``wired``（或反之全部 ``unwired``），
    界面上就会出现一批点不动的控件、或一片没有理由的灰块 —— 而结构测试仍然全绿。
    """
    used = {entry.kind for entry in console_items()}
    assert used == set(KINDS)


def test_unwired_is_the_majority_but_not_everything() -> None:
    """诚实的现状：管理台里**大部分**是未接入，但至少有一项是真的接通了。

    这条断言的作用不是「必须多数未接入」，而是把当前的真实进度写进测试 ——
    将来接通一项，这条会红，提醒改的人顺手更新它，而不是让数字悄悄漂移。
    """
    entries = console_items()
    unwired = [e for e in entries if e.kind == KIND_UNWIRED]
    assert len(unwired) < len(entries)
    assert any(e.kind == KIND_WIRED for e in entries)


def test_the_publish_policy_is_the_one_thing_that_is_wired() -> None:
    """目前唯一接通的是房间公开规则（``POST /room``）。"""
    wired = [entry.key for entry in console_items() if entry.kind == KIND_WIRED]
    assert wired == ["room.publish_policy"]


def test_unwired_items_cite_the_requirement_document() -> None:
    for entry in unwired_items():
        assert entry.doc_ref, f"{entry.key} 是未接入条目，必须指出哪条需求没做"
        assert entry.doc_ref.startswith("§")


def test_hints_are_sentences_not_placeholders() -> None:
    """``hint`` 必须说清「缺什么」，而不是「暂不支持」这类没有信息量的话。

    判据刻意粗糙（长度 + 不得以「暂」开头）：它挡的是最偷懒的写法，
    不去猜「什么样的句子算好句子」。
    """
    for entry in console_items():
        assert len(entry.hint) >= 12, f"{entry.key}: hint 太短，说明不了什么"
        assert not entry.hint.startswith("暂"), f"{entry.key}: 别用「暂不支持」糊过去"


def test_kind_text_covers_every_kind() -> None:
    assert set(KIND_TEXT) == set(KINDS)


def test_item_lookup_returns_none_for_unknown_key() -> None:
    """兜底：查不到不抛错（与 ``color_for_key`` 的约定一致）。"""
    assert item("no.such.key") is None
    assert item("room.publish_policy") is not None


# ── 构造器校验 ──────────────────────────────────────────────────────────


def test_illegal_kind_raises() -> None:
    with pytest.raises(ValueError, match="kind must be one of"):
        ConsoleItem(key="x", title="x", kind="whatever", hint="一句话说明")


def test_empty_hint_raises() -> None:
    with pytest.raises(ValueError, match="hint must not be empty"):
        ConsoleItem(key="x", title="x", kind=KIND_WIRED, hint="")


def test_unwired_without_doc_ref_raises() -> None:
    """不指来源的「未接入」只是一句推诿，所以在构造层就拦住。"""
    with pytest.raises(ValueError, match="must cite a doc_ref"):
        ConsoleItem(key="x", title="x", kind=KIND_UNWIRED, hint="缺的是某个端点")


def test_readonly_does_not_need_a_doc_ref() -> None:
    """只读项不强制引用：它展示的是真值，没有「欠账」要交代。"""
    entry = ConsoleItem(key="x", title="x", kind=KIND_READONLY, hint="展示服务端下发的值")
    assert entry.doc_ref == ""


def test_empty_section_raises() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        ConsoleSection(key="s", title="s", hint="h", initiator_only=True, items=())


def test_actionable_is_true_only_for_wired() -> None:
    for entry in console_items():
        assert entry.actionable == (entry.kind == KIND_WIRED)


# ── 权限：管理功能仅对发起人可见（§模块二.1）────────────────────────────


def test_initiator_sees_every_section() -> None:
    assert len(visible_sections(initiator=True)) == len(CONSOLE_SECTIONS)


def test_participant_sees_only_the_shared_sections() -> None:
    sections = visible_sections(initiator=False)
    assert all(not section.initiator_only for section in sections)
    assert sections, "至少要留一个所有人都看得到的分区，否则页面形态就不统一了"


def test_participant_never_sees_a_section_containing_a_wired_control() -> None:
    """非发起人看不到任何**可操作**的条目 —— 否则「管理功能仅对发起人可见」是空话。"""
    for section in visible_sections(initiator=False):
        assert not any(entry.actionable for entry in section.items)


def test_privacy_facts_are_visible_to_everyone() -> None:
    """隐私保障不是管理功能，它是给所有人的承诺。"""
    keys = {e.key for section in visible_sections(initiator=False) for e in section.items}
    assert "privacy.no_backdoor" in keys
    assert "privacy.local_inference" in keys


def test_the_privacy_section_contains_only_facts() -> None:
    """「隐私保障」分区里不许出现开关或灰块 —— 它整块都是**已经成立**的事实。

    一旦有人往这里塞一个 ``unwired`` 条目（比如「加密存储」），分区标题那句
    「这些不是待办」就变成了假话。加密存储因此放在「感知参数」区，不放这里。
    """
    section = next(s for s in CONSOLE_SECTIONS if s.key == "privacy")
    assert {entry.kind for entry in section.items} == {KIND_FACT}
    assert all(not entry.actionable for entry in section.items)


# ── fact 条目必须是可验证的断言 ─────────────────────────────────────────


def test_the_no_backdoor_claim_is_actually_enforced_by_the_server() -> None:
    """``privacy.no_backdoor`` 说「没有任何角色能绕过公开开关」。

    这句话是可验证的：拿一个**发起人**去看一个未公开的同学，服务端下发的必须是
    掩去后的状态。若哪天有人给发起人开回一个「反正我看得见」的后门，
    界面上的这句承诺就变成了谎话 —— 这条测试会立刻红。
    """
    viewer = _teacher()
    hidden = _student("s07", hidden=True)
    everyone = [viewer, hidden]

    seen = {p.participant_id: p for p in visible_to(viewer, everyone, room=RoomInfo())}
    assert seen["s07"].has_state is False, "发起人竟然看到了别人选择不公开的状态"
    assert seen["s07"].hidden is True
    assert seen["s07"].occupies_cell is True, "掩去状态不等于不占格"


def test_the_forced_publish_rule_is_the_only_way_to_reveal_everyone() -> None:
    """能推翻个人开关的只有**房间规则**，不是某个人的身份。"""
    viewer = _teacher()
    hidden = _student("s07", hidden=True)
    room = RoomInfo(publish_policy=POLICY_MANDATORY)
    seen = {p.participant_id: p for p in visible_to(viewer, [viewer, hidden], room=room)}
    assert seen["s07"].has_state is True


def test_the_local_inference_claim_matches_the_wire_format() -> None:
    """``privacy.local_inference`` / ``minimal_output`` 说「只输出状态标签与置信度」。

    这条拿线路格式来验：信封里**没有任何图像字段**。若将来有人往信封里塞一帧
    缩略图，这两条 fact 就成了假话，而界面上那句「不出图」还在 —— 这里会红。
    """
    payload = _student("s01").to_dict()
    assert set(payload) == {"participant_id", "role", "hidden", "closed", "ts", "state"}
    state = payload["state"]
    assert isinstance(state, dict)
    assert set(state) == {"label", "confidence", "stale", "timestamp", "frame_id"}
