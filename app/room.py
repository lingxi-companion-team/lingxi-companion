"""房间：名称、主题、邀请码与**公开规则**（纯数据 + 纯逻辑）。

为什么单开一个模块而不是塞进 ``app.hub``
------------------------------------------
``app.hub`` 的职责是「在线表 + 按观看者装配 payload」。房间元数据（叫什么、什么主题、
邀请码是什么、公开规则是哪一档）与在线表是**两件事**：前者在会话开始时定下、很少变，
后者每帧都在动。混在一起会让 hub 从「装配」滑向「什么都管」。

更重要的是：**公开规则是一条可测的判定规则**，而 ``app.hub`` 里没有复杂逻辑的容身之处
（GUI 层与 hub 的装配路径都刻意做薄）。规则放这里，配一套独立单测。

为什么用「公开规则」而不是「给发起人开一个看得见全部的后门」
------------------------------------------------------------
需求文档 §模块二.1 要求「界面统一化……仅通过权限区分功能可见性」，
§模块二.2 要求「可自主设置是否向房间公开自己的学习状态，默认仅本人可见」，
而 §模块三.2 给了发起人一条**规则层面**的杠杆：「可设置房间状态公开规则：
可选『全员强制公开』『个人自主选择』两种模式」。

三者合起来指向同一个设计：**发起人的权力体现在「定规则」，而不是「绕过隐私」**。
若给发起人一个「反正我看得见」的后门，§模块二.2 的自主开关对他就形同虚设 ——
那正是「隐私原生」最不该出现的东西。

（旧实现里 ``visible_to`` 有一条 ``if viewer.is_teacher: return people`` 的分支，
本次按需求文档移除。见 ``app/present/visibility.py``。）
"""

from __future__ import annotations

from dataclasses import dataclass, replace

__all__ = [
    "POLICIES",
    "POLICY_MANDATORY",
    "POLICY_OPTIONAL",
    "POLICY_TEXT",
    "RoomInfo",
    "is_valid_policy",
]

#: **个人自主选择**（默认）：每个人自己决定是否向房间公开状态。
POLICY_OPTIONAL = "optional"
#: **全员强制公开**：房间内所有人的状态都公开，个人开关被忽略。
POLICY_MANDATORY = "mandatory"

POLICIES: tuple[str, ...] = (POLICY_OPTIONAL, POLICY_MANDATORY)

#: 规则的中文展示名。放在这里而不是 UI 层：它是**展示规则**，
#: 而 UI 层（``app/web/``）整体 omit 于覆盖率 —— 写在那儿没有护栏。
POLICY_TEXT: dict[str, str] = {
    POLICY_OPTIONAL: "个人自主选择",
    POLICY_MANDATORY: "全员强制公开",
}


def is_valid_policy(policy: str) -> bool:
    """规则名是否合法。未知值由调用方决定退回默认还是报错。"""
    return policy in POLICIES


@dataclass(frozen=True)
class RoomInfo:
    """一间学习房间的元数据。

    Attributes:
        name: 房间名称（发起人自定义）。空串表示未命名 —— 展示层据此显示占位文案。
        topic: 学习主题。空串表示未填写。
        invite_code: 邀请码；``""`` 表示**尚未生成**（对应需求文档 §模块三.1）。
            展示层在空串时显示「未生成」而不是编一个假码。
        publish_policy: :data:`POLICY_OPTIONAL` 或 :data:`POLICY_MANDATORY`。

    ``publish_policy`` 在 ``__post_init__`` 里校验：非法值**抛错**而不是静默退回。
    这条规则决定「别人的状态给不给你看」，静默降级会让一间配错规则的房间
    悄悄退回默认口径，而调用方以为设成了强制公开 —— 这类错误必须响。
    """

    name: str = ""
    topic: str = ""
    invite_code: str = ""
    publish_policy: str = POLICY_OPTIONAL

    def __post_init__(self) -> None:
        if not is_valid_policy(self.publish_policy):
            allowed = ", ".join(POLICIES)
            raise ValueError(
                f"publish_policy must be one of {allowed}, got {self.publish_policy!r}"
            )

    @property
    def forces_publish(self) -> bool:
        """本房间是否**强制公开**所有人的状态（个人开关失效）。"""
        return self.publish_policy == POLICY_MANDATORY

    @property
    def has_invite_code(self) -> bool:
        """是否已生成邀请码。未生成时展示层不该编造一个。"""
        return bool(self.invite_code)

    def with_policy(self, policy: str) -> RoomInfo:
        """换一档公开规则（其余字段不变）。非法值由 ``__post_init__`` 抛错。"""
        return replace(self, publish_policy=policy)
