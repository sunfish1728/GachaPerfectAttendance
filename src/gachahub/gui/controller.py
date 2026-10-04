"""GUI 與核心之間的控制器：持有核心物件，在背景執行緒跑任務鏈，以 Qt 信號回報。

所有信號都在主執行緒送達（跨執行緒 emit 會自動排入事件佇列），頁面可直接更新 UI。
"""

from __future__ import annotations

import logging
import threading

from PySide6.QtCore import QObject, Signal

from ..core.config import ChainStore, Paths
from ..core.context import RunContext
from ..core.models import ChainReport, TaskChain
from ..core.registry import AdapterRegistry
from ..core.runner import ChainRunner


class AppController(QObject):
    chainsChanged = Signal()
    chainRenamed = Signal(str, str)  # (舊名稱, 新名稱)
    runStarted = Signal(str)  # 任務鏈名稱
    runEvent = Signal(str, dict)  # 見 RunContext.on_event
    runLog = Signal(str)
    runFinished = Signal(object)  # ChainReport
    historyChanged = Signal()
    notifyResults = Signal(list)  # [(管道名稱, 成功, 訊息)]：通知送出結果（失敗時 GUI 提示）

    def __init__(self, paths: Paths, parent: QObject | None = None):
        super().__init__(parent)
        self.paths = paths
        self.registry = AdapterRegistry()
        self.registry.load_dir(paths.adapters)
        self.store = ChainStore(paths)
        self.runner = ChainRunner(self.registry)
        from ..core.history import HistoryStore

        self.history = HistoryStore(paths.root / "data" / "history.db")
        self.notify_path = paths.root / "data" / "notify.yaml"
        self.last_report: ChainReport | None = None
        self.current_chain: str | None = None
        self._ctx: RunContext | None = None
        self._thread: threading.Thread | None = None
        self._running = False  # 於 runFinished 送出前清除，槽函式中讀到的狀態才一致

    # --- 任務鏈存取 ---

    def chains(self) -> list[TaskChain]:
        return self.store.list()

    def save_chain(self, chain: TaskChain, old_name: str | None = None) -> None:
        self.store.save(chain)
        if old_name and old_name != chain.name:
            self.store.delete(old_name)
            self.chainRenamed.emit(old_name, chain.name)
        self.chainsChanged.emit()

    def delete_chain(self, name: str) -> None:
        self.store.delete(name)
        self.chainsChanged.emit()

    def needs_admin(self, chain: TaskChain) -> bool:
        """任務鏈中是否有啟用的步驟使用需要管理員權限的適配器。"""
        from ..core.registry import BUILTIN

        for s in chain.steps:
            info = self.registry.infos.get(s.adapter)
            if s.enabled and info and getattr(BUILTIN.get(info.base), "requires_admin", False):
                return True
        return False

    # --- 執行 ---

    @property
    def running(self) -> bool:
        return self._running

    def start_chain(self, chain: TaskChain, trigger: str = "manual", schedule_id: str | None = None) -> bool:
        if self.running or self.runner.busy:
            return False
        self._ctx = RunContext(
            work_dir=self.paths.runtime / "work",
            on_log=self.runLog.emit,
            on_event=self.runEvent.emit,
            failure_dir=self.paths.runtime / "failures",
        )
        self.current_chain = chain.name
        self._running = True
        self._thread = threading.Thread(
            target=self._work, args=(chain, self._ctx, trigger, schedule_id), name="chain-runner", daemon=True
        )
        self.runStarted.emit(chain.name)
        self._thread.start()
        return True

    def request_stop(self) -> None:
        """緊急停止入口（按鈕、托盤，之後的全域熱鍵也呼叫這裡）。"""
        if self._ctx is not None and self.running:
            self.runLog.emit("收到停止要求，正在終止並還原…")
            self._ctx.cancel_event.set()

    def wait_stopped(self, timeout: float = 15.0) -> bool:
        if self._thread is not None:
            self._thread.join(timeout)
        return not (self._thread and self._thread.is_alive())

    def _work(self, chain: TaskChain, ctx: RunContext, trigger: str, schedule_id: str | None) -> None:
        try:
            report = self.runner.run(chain, ctx)
        except Exception as e:  # 不讓背景執行緒的例外悄悄消失
            ctx.log(f"任務鏈執行失敗：{e!r}")
            report = None
        if report is not None:
            try:
                self.history.record(report, trigger=trigger, schedule_id=schedule_id)
            except Exception:
                logging.getLogger("gachahub").exception("寫入歷史紀錄失敗")
        self.last_report = report
        self.current_chain = None
        self._running = False
        self.runFinished.emit(report)
        self.historyChanged.emit()
        if report is not None:
            # 通知可能要數秒（網路），放在 runFinished 之後，不拖慢介面更新
            from ..core.notify import format_report

            event, title, body = format_report(report, trigger)
            self._broadcast(event, title, body)

    # --- 通知 ---

    def notify_config(self):
        from ..core.notify import load_config

        return load_config(self.notify_path)

    def save_notify_config(self, config) -> None:
        from ..core.notify import save_config

        save_config(self.notify_path, config)

    def _broadcast(self, event: str, title: str, body: str) -> list:
        from ..core.notify import broadcast

        try:
            results = broadcast(self.notify_config(), event, title, body)
        except Exception as e:
            logging.getLogger("gachahub").exception("通知發送失敗")
            results = [("通知", False, str(e))]
        for name, ok, msg in results:
            logging.getLogger("gachahub").info("通知 %s：%s %s", name, "成功" if ok else "失敗", msg)
        if any(not ok for _, ok, _ in results):
            self.notifyResults.emit(results)
        return results

    def notify_async(self, event: str, title: str, body: str) -> None:
        """在背景執行緒送出通知（例如排程被略過）。"""
        threading.Thread(target=self._broadcast, args=(event, title, body), daemon=True).start()
