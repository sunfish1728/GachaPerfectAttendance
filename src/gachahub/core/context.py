"""執行上下文：取消訊號、日誌回呼、截止時間。"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("gachahub")


class Cancelled(Exception):
    """使用者取消（緊急停止）時拋出。"""


class StepTimeout(Exception):
    """步驟超過 timeout 時拋出。"""


def capture_if_failing(ctx: "RunContext") -> None:
    """在適配器的 finally（關閉腳本程序之前）呼叫：若正因失敗／逾時／卡死離開，先保存現場截圖。
    使用者取消不截圖。"""
    import sys

    exc = sys.exc_info()[1]
    if exc is not None and not isinstance(exc, Cancelled):
        ctx.capture_failure()


@dataclass
class RunContext:
    work_dir: Path  # 執行期暫存目錄（標記檔、臨時配置等），位於專案 runtime/ 下
    cancel_event: threading.Event = field(default_factory=threading.Event)
    on_log: Callable[[str], None] | None = None
    # 進度事件（供 GUI 使用）：("chain_start", {"chain": 名稱, "steps": [步驟名稱]}) / ("step_start", {"index": i, "name": ...})
    # / ("step_end", {"index": i, "result": StepResult})；於工作執行緒呼叫
    on_event: Callable[[str, dict], None] | None = None
    deadline: float | None = None  # time.monotonic() 截止點，由 runner 依步驟 timeout 設定
    failure_dir: Path | None = None  # None 停用失敗截圖
    # 由 runner 在每次嘗試前設定；適配器在失敗、關閉腳本程序「之前」呼叫 capture_failure() 保存現場
    failure_hook: Callable[[], None] | None = field(default=None, repr=False)

    def capture_failure(self) -> None:
        """保存失敗現場截圖（每次嘗試最多一次，之後的呼叫不做事）。"""
        hook, self.failure_hook = self.failure_hook, None
        if hook is not None:
            hook()

    def log(self, msg: str) -> None:
        log.info(msg)
        if self.on_log:
            self.on_log(msg)

    def emit(self, kind: str, **data) -> None:
        if self.on_event:
            try:
                self.on_event(kind, data)
            except Exception:
                log.exception("on_event 回呼失敗")

    @property
    def cancelled(self) -> bool:
        return self.cancel_event.is_set()

    def remaining(self) -> float | None:
        if self.deadline is None:
            return None
        return self.deadline - time.monotonic()

    def check(self) -> None:
        """在等待迴圈中呼叫：已取消或逾時就拋例外。"""
        if self.cancelled:
            raise Cancelled()
        rem = self.remaining()
        if rem is not None and rem <= 0:
            raise StepTimeout()

    def sleep(self, seconds: float) -> None:
        """可被取消、不超過截止時間的 sleep。"""
        rem = self.remaining()
        if rem is not None:
            seconds = max(0.0, min(seconds, rem))
        if self.cancel_event.wait(seconds):
            raise Cancelled()
        self.check()
