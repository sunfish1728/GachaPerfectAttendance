"""系統托盤：顯示視窗、快速執行、緊急停止、離開。"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QSystemTrayIcon
from qfluentwidgets import Action, FluentIcon as FIF, SystemTrayMenu

from .controller import AppController
from .widgets import chain_summary


class Tray(QSystemTrayIcon):
    showRequested = Signal()
    quitRequested = Signal()
    runRequested = Signal(object)  # 交給主視窗處理（權限檢查等）
    pauseToggled = Signal()

    def __init__(self, controller: AppController, icon: QIcon, parent=None):
        super().__init__(icon, parent)
        self.controller = controller
        self.scheduler = None  # 由主視窗設定
        self.setToolTip("二游腳本集合站 — 待命中")
        self.menu = SystemTrayMenu(parent=parent)
        self.setContextMenu(self.menu)
        self.menu.aboutToShow.connect(self._rebuild)
        self._rebuild()
        self.activated.connect(self._on_activated)
        controller.runStarted.connect(lambda n: self.setToolTip(f"二游腳本集合站 — 執行中：{n}"))
        controller.runEvent.connect(self._on_event)
        controller.runFinished.connect(self._on_finished)

    def _rebuild(self) -> None:
        m = self.menu
        m.clear()
        m.addAction(Action(FIF.HOME, "顯示主視窗", triggered=lambda *_: self.showRequested.emit()))
        m.addSeparator()
        running = self.controller.running
        chains = self.controller.chains()
        if chains:
            for c in chains:
                a = Action(FIF.PLAY, f"執行「{c.name}」", triggered=lambda _=False, c=c: self.runRequested.emit((c, "tray")))
                a.setToolTip(chain_summary(c))
                a.setEnabled(not running and any(s.enabled for s in c.steps))
                m.addAction(a)
        else:
            a = Action(FIF.PLAY, "尚無任務鏈")
            a.setEnabled(False)
            m.addAction(a)
        stop = Action(FIF.CANCEL, "緊急停止", triggered=lambda *_: self.controller.request_stop())
        stop.setEnabled(running)
        m.addAction(stop)
        if self.scheduler is not None:
            paused = self.scheduler.paused
            m.addAction(Action(FIF.PLAY if paused else FIF.PAUSE, "恢復排程" if paused else "今日暫停排程",
                               triggered=lambda *_: self.pauseToggled.emit()))
        m.addSeparator()
        m.addAction(Action(FIF.CLOSE, "離開", triggered=lambda *_: self.quitRequested.emit()))

    def _on_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.showRequested.emit()

    def _on_event(self, kind: str, data: dict) -> None:
        if kind == "step_start" and self.controller.current_chain:
            self.setToolTip(f"二游腳本集合站 — {self.controller.current_chain}\n第 {data['index'] + 1} 步：{data['name']}")

    def _on_finished(self, report) -> None:
        self.setToolTip("二游腳本集合站 — 待命中")
        if report is None:
            self.showMessage("任務鏈出錯", "詳見執行頁日誌", QSystemTrayIcon.MessageIcon.Critical)
        elif report.cancelled:
            self.showMessage(f"已停止：{report.chain}", "系統狀態已還原", QSystemTrayIcon.MessageIcon.Information)
        elif report.ok:
            self.showMessage(f"完成：{report.chain}", f"{len(report.results)} 個步驟全部成功", QSystemTrayIcon.MessageIcon.Information)
        else:
            bad = [r.step for r in report.results if r.status.value not in ("success", "skipped")]
            self.showMessage(f"有步驟失敗：{report.chain}", "、".join(bad) or "任務鏈已中止", QSystemTrayIcon.MessageIcon.Warning)
