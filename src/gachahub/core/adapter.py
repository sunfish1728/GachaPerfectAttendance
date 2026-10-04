"""適配器介面。每種腳本接法 = 一個適配器，主程式不寫死任何腳本細節。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from .context import RunContext


class AdapterError(Exception):
    """適配器回報的失敗（參數錯誤、腳本執行失敗等）。"""


@dataclass
class TaskOption:
    """腳本內可選的任務（供 GUI 下拉選單）。value 存入步驟參數，label 顯示給使用者。"""

    value: str
    label: str


class Adapter(ABC):
    id: ClassVar[str]
    display_name: ClassVar[str] = ""

    def __init__(self, defaults: dict[str, Any] | None = None):
        # defaults 來自宣告式 YAML 適配器，步驟參數會覆蓋它
        self.defaults = dict(defaults or {})

    def merged(self, params: dict[str, Any]) -> dict[str, Any]:
        return {**self.defaults, **params}

    def validate(self, params: dict[str, Any]) -> None:
        """參數檢查，不合法時拋 AdapterError。預設不檢查。"""

    @abstractmethod
    def run(self, ctx: RunContext, params: dict[str, Any]) -> str:
        """執行一次。成功回傳說明文字；失敗拋 AdapterError；
        須定期呼叫 ctx.check()，被取消或逾時時讓 Cancelled/StepTimeout 傳出，
        並在離開前清理自己啟動的程序。"""

    def cleanup(self, ctx: RunContext, params: dict[str, Any]) -> None:
        """runner 在每次嘗試結束後（不論成敗）呼叫。"""

    def script_version(self, params: dict) -> str | None:
        return None

    # --- 供 GUI 使用的自省介面（可選實作） ---

    requires_admin: ClassVar[bool] = False  # 腳本需要系統管理員權限（未提權時 GUI 會提示）
    # GUI 表單描述：
    uses_install_dir: ClassVar[bool] = False  # 以 params["install_dir"] 指定安裝資料夾（顯示資料夾選擇與自動偵測）
    task_param: ClassVar[str | None] = None  # list_tasks() 結果寫入哪個參數（例如 "task"、"instance"）
    task_label: ClassVar[str] = "任務"
    bool_options: ClassVar[list[tuple[str, str, bool]]] = []  # (參數名, 顯示文字, 預設值) → 以勾選框呈現

    def detect(self) -> list[Path]:
        """自動偵測已安裝位置（安裝資料夾），找不到回傳空列表。不可耗時超過數秒。"""
        return []

    def list_tasks(self, params: dict[str, Any]) -> list[TaskOption]:
        """列出腳本可選任務；不支援或讀取失敗回傳空列表（不拋例外）。"""
        return []
