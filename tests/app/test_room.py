"""``app.room`` 的测试：房间元数据 + 公开规则。

这组测试守的是需求文档 §模块三.2 的那条杠杆：「发起人可设置房间状态公开规则：
可选『全员强制公开』『个人自主选择』两种模式」。
"""

from __future__ import annotations

import pytest

from app.room import (
    POLICIES,
    POLICY_MANDATORY,
    POLICY_OPTIONAL,
    POLICY_TEXT,
    RoomInfo,
    is_valid_policy,
)


def test_defaults_are_conservative_and_blank() -> None:
    """默认 = **个人自主选择** + 三个空字段。

    默认必须是**最保守**的那档：一间刚建好的房间不该替成员决定公开状态。
    空字段（而不是编一个假邀请码/假房间名）让 UI 能如实显示「未生成」「未命名」。
    """
    room = RoomInfo()
    assert room.publish_policy == POLICY_OPTIONAL
    assert room.name == ""
    assert room.topic == ""
    assert room.invite_code == ""
    assert room.has_invite_code is False


def test_forces_publish_only_for_mandatory() -> None:
    assert RoomInfo(publish_policy=POLICY_MANDATORY).forces_publish is True
    assert RoomInfo(publish_policy=POLICY_OPTIONAL).forces_publish is False


@pytest.mark.parametrize("policy", POLICIES)
def test_every_policy_has_a_display_name(policy: str) -> None:
    """每个合法规则都要有中文展示名 —— 否则 UI 上会出现一个英文/空白的下拉项。"""
    assert policy in POLICY_TEXT
    assert POLICY_TEXT[policy]


def test_policy_text_covers_exactly_the_policies() -> None:
    assert set(POLICY_TEXT) == set(POLICIES)


@pytest.mark.parametrize("policy", POLICIES)
def test_valid_policy_accepts_known_values(policy: str) -> None:
    assert is_valid_policy(policy) is True


@pytest.mark.parametrize("policy", ["", "everyone", "MANDATORY", "force", "optional "])
def test_invalid_policy_is_rejected(policy: str) -> None:
    """未知值一律判非法 —— 大小写与尾空格都不宽容，避免「看起来设上了其实没设」。"""
    assert is_valid_policy(policy) is False


def test_illegal_policy_raises_instead_of_silently_falling_back() -> None:
    """**这是本模块最重要的一条**：非法规则名必须抛错。

    这条规则决定「别人的状态给不给你看」。静默降级会让一间配错规则的房间
    悄悄退回默认口径，而调用方以为设成了强制公开 —— 这类错误必须响。
    """
    with pytest.raises(ValueError, match="publish_policy"):
        RoomInfo(publish_policy="everyone-can-see")


def test_with_policy_keeps_the_other_fields() -> None:
    room = RoomInfo(name="周三晚自习", topic="线性代数", invite_code="LX-3F9K")
    switched = room.with_policy(POLICY_MANDATORY)
    assert switched.publish_policy == POLICY_MANDATORY
    assert switched.name == "周三晚自习"
    assert switched.topic == "线性代数"
    assert switched.invite_code == "LX-3F9K"
    assert room.publish_policy == POLICY_OPTIONAL, "原对象必须保持不可变"


def test_with_policy_rejects_an_illegal_value() -> None:
    with pytest.raises(ValueError, match="publish_policy"):
        RoomInfo().with_policy("nope")


def test_has_invite_code_is_true_only_for_a_non_empty_code() -> None:
    assert RoomInfo(invite_code="LX-3F9K").has_invite_code is True
    assert RoomInfo(invite_code="").has_invite_code is False
