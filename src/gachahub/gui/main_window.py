"""主視窗：導覽、頁面、托盤與關閉行為。"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QLinearGradient, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QApplication
from qfluentwidgets import (
    FluentIcon as FIF,
    FluentWindow,
    InfoBar,
    InfoBarPosition,
    MessageBox,
    NavigationItemPosition,
)

from .chain_page import ChainPage
from .elevation import is_admin
from .controller import AppController
from .home_page import HomePage
from .countdown import CountdownToast
from .history_page import HistoryPage
from .notify_page import NotifyPage
from .run_page import RunPage
from .schedule_page import SchedulePage
from .scheduler_service import SchedulerService
from .settings_page import SettingsPage, cfg
from .tray import Tray


ICON_FILE = Path(__file__).resolve().parents[3] / "assets" / "icon.ico"


def make_app_icon() -> QIcon:
    """應用程式圖示：優先使用 assets/icon.ico（多尺寸），缺檔時改用程式內繪製的備用圖示。"""
    if ICON_FILE.exists():
        icon = QIcon(str(ICON_FILE))
        if not icon.isNull():
            return icon
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        grad = QLinearGradient(0, 0, size, size)
        grad.setColorAt(0, QColor("#9B7BFF"))
        grad.setColorAt(1, QColor("#5B3FD9"))
        p.setBrush(grad)
        p.setPen(Qt.PenStyle.NoPen)
        r = size * 0.22
        p.drawRoundedRect(QRectF(0, 0, size, size), r, r)
        tri = QPainterPath()
        s = size
        tri.moveTo(QPointF(s * 0.40, s * 0.30))
        tri.lineTo(QPointF(s * 0.72, s * 0.50))
        tri.lineTo(QPointF(s * 0.40, s * 0.70))
        tri.closeSubpath()
        p.setBrush(QColor("white"))
        p.drawPath(tri)
        p.end()
        icon.addPixmap(pm)
    return icon


class MainWindow(FluentWindow):
    hotkeyPressed = Signal()  # 由熱鍵執行緒 emit，自動排入主執行緒

    def __init__(self, controller: AppController):
        super().__init__()
        self.controller = controller
        self._quitting = False
        self.instance_server = None  # 由 __main__ 設定
        self.instance_key = ""
        self.app_icon = make_app_icon()
        self.setWindowIcon(self.app_icon)
        self.setWindowTitle("二游腳本集合站")
        self.resize(1080, 720)
        self.setMinimumSize(860, 600)

        self.scheduler = SchedulerService(controller, self)
        self.homePage = HomePage(controller, self)
        self.homePage.set_scheduler(self.scheduler)
        self.chainPage = ChainPage(controller, self)
        self.schedulePage = SchedulePage(self.scheduler, self)
        self.historyPage = HistoryPage(controller, self)
        self.notifyPage = NotifyPage(controller, self)
        self.runPage = RunPage(controller, self)
        self.settingsPage = SettingsPage(controller, self)

        self.addSubInterface(self.homePage, FIF.HOME, "首頁")
        self.addSubInterface(self.chainPage, FIF.ROBOT, "任務鏈")
        self.addSubInterface(self.schedulePage, FIF.CALENDAR, "排程")
        self.addSubInterface(self.runPage, FIF.COMMAND_PROMPT, "執行")
        self.addSubInterface(self.historyPage, FIF.HISTORY, "歷史")
        self.addSubInterface(self.notifyPage, FIF.RINGER, "通知", NavigationItemPosition.BOTTOM)
        self.addSubInterface(self.settingsPage, FIF.SETTING, "設定", NavigationItemPosition.BOTTOM)
        self.navigationInterface.setExpandWidth(180)

        self.homePage.runRequested.connect(self.run_chain)
        self.homePage.openChain.connect(self._open_chain)
        self.homePage.showRun.connect(lambda: self.switchTo(self.runPage))
        self.homePage.createChain.connect(self._create_chain)
        self.homePage.elevateRequested.connect(self.restart_as_admin)
        self.chainPage.runRequested.connect(self.run_chain)

        self.tray = Tray(controller, self.app_icon, self)
        self.tray.showRequested.connect(self.bring_to_front)
        self.tray.quitRequested.connect(self.quit)
        self.tray.runRequested.connect(self.run_chain)
        self.tray.pauseToggled.connect(lambda: self.scheduler.resume() if self.scheduler.paused else self.scheduler.pause_today())
        self.tray.scheduler = self.scheduler
        self.tray.show()

        # 排程
        self._toast: CountdownToast | None = None
        self.scheduler.countdownRequested.connect(self._show_countdown)
        self.scheduler.notice.connect(lambda t, m: self.tray.showMessage(t, m, self.app_icon))
        self.scheduler.runRequested.connect(self.run_chain)
        self.scheduler.start()

        # 緊急停止熱鍵
        self._hotkey = None
        self.hotkeyPressed.connect(self._on_hotkey)
        self.settingsPage.hotkeyChanged.connect(self.apply_hotkey)
        self.settingsPage.wizardRequested.connect(self.open_wizard)
        controller.runEvent.connect(self._on_run_event)
        controller.notifyResults.connect(self._on_notify_results)
        self.apply_hotkey()
        if is_admin():
            self.sync_wake_tasks(silent=True)
        # 首次使用：沒有任何任務鏈時開啟嚮導；啟動後靜默檢查更新
        from PySide6.QtCore import QTimer

        QTimer.singleShot(800, self._first_run)
        if cfg.autoCheckUpdate.value and cfg.appRepo.value:
            from .update_ui import check_app_update

            QTimer.singleShot(5000, lambda: check_app_update(cfg.appRepo.value, self, silent=True))

        self._center()

    # --- 動作 ---

    def run_chain(self, chain) -> None:
        trigger = "manual"
        if isinstance(chain, tuple):  # 托盤送來 (chain, "tray")
            chain, trigger = chain
        if not any(s.enabled for s in chain.steps):
            InfoBar.warning("無法執行", "這條任務鏈沒有啟用的步驟", parent=self, position=InfoBarPosition.TOP)
            return
        if self.controller.needs_admin(chain) and not is_admin():
            self.bring_to_front()
            box = MessageBox(
                "需要系統管理員權限",
                "這條任務鏈中的腳本需要管理員權限。未提權時腳本會跳出 UAC 提示，且本程式無法監控或停止它。\n\n"
                "建議先以管理員身分重新啟動本程式（可在設定中改為每次自動提權）。",
                self,
            )
            box.yesButton.setText("仍要執行")
            box.cancelButton.setText("取消")
            if not box.exec():
                return
        if not self.controller.start_chain(chain, trigger=trigger):
            InfoBar.warning("無法執行", "已有任務鏈在執行中", parent=self, position=InfoBarPosition.TOP)
            return
        self.switchTo(self.runPage)

    # --- 嚮導、相容性、通知結果 ---

    def _first_run(self) -> None:
        if not cfg.wizardDone.value and not self.controller.chains() and self.isVisible():
            self.open_wizard()

    def open_wizard(self) -> None:
        from .wizard import SetupWizard

        self.bring_to_front()
        wiz = SetupWizard(self.controller, self.scheduler, self)
        wiz.exec()
        cfg.set(cfg.wizardDone, True)
        if wiz.created:
            self.switchTo(self.chainPage)
            self.chainPage.select(wiz.created)
            InfoBar.success("已建立任務鏈", wiz.created, duration=4000, parent=self, position=InfoBarPosition.TOP)

    def _on_run_event(self, kind: str, data: dict) -> None:
        if kind == "compat_warning":
            msg = data.get("message", "")
            InfoBar.warning("腳本版本可能不相容", msg, duration=10000, parent=self, position=InfoBarPosition.TOP)
            self.tray.showMessage("腳本版本可能不相容", msg, self.app_icon)

    def _on_notify_results(self, results: list) -> None:
        bad = [f"{name}：{msg}" for name, ok, msg in results if not ok]
        if bad:
            InfoBar.warning("部分通知沒有送出", "；".join(bad)[:200], duration=8000, parent=self,
                            position=InfoBarPosition.TOP)

    # --- 排程倒數 ---

    def _show_countdown(self, item) -> None:
        if self._toast is not None:
            self._toast.dismiss()
        toast = CountdownToast(item, cfg.countdownSeconds.value, cfg.presenceDeferMinutes.value)
        toast.startNow.connect(self.scheduler.confirm)
        toast.postponeRequested.connect(self.scheduler.postpone)
        toast.skipRequested.connect(self.scheduler.skip)
        self._toast = toast
        toast.popup()

    # --- 熱鍵 ---

    def apply_hotkey(self) -> None:
        from ..core.hotkey import GlobalHotkey, format_hotkey

        if self._hotkey is not None:
            self._hotkey.stop()
            self._hotkey = None
        if not cfg.hotkeyEnabled.value:
            return
        try:
            hk = GlobalHotkey(cfg.hotkey.value, self.hotkeyPressed.emit)
            ok, msg = hk.start()
        except ValueError as e:
            ok, msg = False, str(e)
        if ok:
            self._hotkey = hk
            logging.getLogger("gachahub").info("緊急停止熱鍵：%s", format_hotkey(cfg.hotkey.value))
        else:
            InfoBar.warning("緊急停止熱鍵無法使用", msg, duration=6000, parent=self, position=InfoBarPosition.TOP)

    def _on_hotkey(self) -> None:
        if self.controller.running:
            self.controller.request_stop()
            self.tray.showMessage("緊急停止", "正在終止腳本並還原系統狀態", self.app_icon)
        elif self._toast is not None and self.scheduler.counting is not None:
            self._toast.dismiss()
            self.scheduler.skip()
            self.tray.showMessage("已取消排程", "倒數中的排程已略過", self.app_icon)

    # --- 喚醒電腦（Windows 工作排程器） ---

    def sync_wake_tasks(self, silent: bool = False) -> None:
        from ..core import wintask

        schedules = self.scheduler.schedules()
        wants = any(s.wake_computer and s.enabled for s in schedules)
        if not is_admin():
            if wants and not silent:
                InfoBar.warning(
                    "需要系統管理員權限", "建立喚醒工作需要以管理員身分執行本程式；排程本身仍會在本程式開啟時觸發",
                    duration=8000, parent=self, position=InfoBarPosition.TOP,
                )
            return
        try:
            msgs = wintask.sync(schedules, sys.executable, str(self.controller.paths.root))
        except Exception as e:
            msgs = [f"同步喚醒工作失敗：{e}"]
        for m in msgs:
            logging.getLogger("gachahub").info(m)
        if msgs and not silent:
            InfoBar.success("已更新喚醒設定", "；".join(msgs[:3]), duration=4000, parent=self, position=InfoBarPosition.TOP)

    def restart_as_admin(self) -> None:
        from .elevation import relaunch_as_admin

        if self.controller.running:
            InfoBar.warning("請稍候", "任務執行中，結束後再重新啟動", parent=self, position=InfoBarPosition.TOP)
            return
        if self.instance_server is not None:
            self.instance_server.close()
        if relaunch_as_admin():
            self._quitting = True
            self.tray.hide()
            QApplication.quit()
            return
        if self.instance_server is not None:
            self.instance_server.listen(self.instance_key)
        InfoBar.info("已取消", "未取得系統管理員權限", parent=self, position=InfoBarPosition.TOP)

    def _open_chain(self, name: str) -> None:
        self.switchTo(self.chainPage)
        self.chainPage.select(name)

    def _create_chain(self) -> None:
        self.switchTo(self.chainPage)
        self.chainPage.new_chain()

    def bring_to_front(self) -> None:
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()

    def _center(self) -> None:
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            self.move(geo.center() - self.rect().center())

    # --- 關閉與離開 ---

    def closeEvent(self, e) -> None:
        if not self._quitting and cfg.closeToTray.value and self.tray.isVisible():
            e.ignore()
            self.hide()
            if not cfg.trayHintShown.value:
                self.tray.showMessage("仍在背景執行", "可從系統托盤圖示重新開啟或離開", self.app_icon)
                cfg.set(cfg.trayHintShown, True)
            return
        if not self._quitting and not self._confirm_quit():
            e.ignore()
            return
        self._shutdown()
        e.accept()
        QApplication.quit()

    def quit(self) -> None:
        if not self._confirm_quit():
            return
        self._quitting = True
        self._shutdown()
        self.tray.hide()
        QApplication.quit()

    def _confirm_quit(self) -> bool:
        if not self.controller.running:
            return True
        self.bring_to_front()
        box = MessageBox("任務執行中", "離開會停止目前的任務鏈並還原系統狀態，確定離開？", self)
        box.yesButton.setText("停止並離開")
        box.cancelButton.setText("取消")
        return bool(box.exec())

    def _shutdown(self) -> None:
        if self._hotkey is not None:
            self._hotkey.stop()
            self._hotkey = None
        if self._toast is not None:
            self._toast.dismiss()
        if self.controller.running:
            self.controller.request_stop()
            self.controller.wait_stopped(15.0)
