"""鉤子介面。

前置鉤子（pre_hooks）：apply() 改變系統狀態並回傳還原所需的 state，
任務鏈結束（含失敗、取消、例外）後 runner 會以反序呼叫 restore(state)。
後置動作（post_actions）：只呼叫 apply()，例如關閉程式、關機。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar

from ..core.context import RunContext


class Hook(ABC):
    type: ClassVar[str]
    display_name: ClassVar[str] = ""

    def __init__(self, params: dict[str, Any] | None = None):
        self.params = dict(params or {})

    @abstractmethod
    def apply(self, ctx: RunContext) -> Any:
        """執行並回傳還原用的 state（不需還原則回傳 None）。"""

    def restore(self, ctx: RunContext, state: Any) -> None:
        """依 apply() 回傳的 state 還原。預設不做事。"""


HOOKS: dict[str, type[Hook]] = {}


def register(cls: type[Hook]) -> type[Hook]:
    HOOKS[cls.type] = cls
    return cls


def create_hook(type_: str, params: dict[str, Any]) -> Hook:
    # 延遲匯入內建鉤子，確保已註冊
    from . import builtin  # noqa: F401

    if type_ not in HOOKS:
        raise KeyError(f"找不到鉤子：{type_}")
    return HOOKS[type_](params)
