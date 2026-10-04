"""首頁：目前狀態、快速執行卡片、上次執行摘要。"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    ElevatedCardWidget,
    FlowLayout,
    FluentIcon as FIF,
    IconWidget,
    InfoBar,
    InfoBarIcon,
    PrimaryToolButton,
    ProgressBar,
    PushButton,
    SmoothScrollArea,
    StrongBodyLabel,
    SubtitleLabel,
    TitleLabel,
    isDarkTheme,
    qconfig,
    themeColor,
)

from ..core.models import ChainReport, StepStatus, TaskChain
from .controller import AppController
from .elevation import is_admin
from .widgets import EmptyState, SectionHeader, StatusBadge, chain_summary, fmt_duration, muted_caption

WEEKDAYS = "一二三四五六日"


def _greeting() -> str:
    h = datetime.now().hour
    if h < 5:
        return "夜深了"
    if h < 11:
        return "早安"
    if h < 18:
        return "午安"
    return "晚安"


class ChainCard(ElevatedCardWidget):
    runClicked = Signal(object)
    openClicked = Signal(str)

    def __init__(self, chain: TaskChain, today=None, parent=None):
        super().__init__(parent)
        self.chain = chain
        self.setFixedSize(248, 112)
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 14, 12, 12)
        top = QHBoxLayout()
        icon = IconWidget(FIF.ROBOT, self)
        icon.setFixedSize(20, 20)
        top.addWidget(icon)
        top.addSpacing(6)
        name = StrongBodyLabel(chain.name, self)
        top.addWidget(name, 1)
        v.addLayout(top)
        cap = muted_caption(chain_summary(chain), self)
        cap.setWordWrap(True)
        v.addWidget(cap)
        v.addStretch(1)
        bottom = QHBoxLayout()
        if today is not None:
            # today = (last_status, runs)：今天的執行結果
            status, runs = today
            badge = StatusBadge(status, self)
            badge.setText({"success": "今天已完成", "failed": "今天失敗", "cancelled": "今天已停止"}.get(status, status)
                          + (f" ×{runs}" if runs > 1 else ""))
            bottom.addWidget(badge)
        else:
            bottom.addWidget(muted_caption("今天尚未執行", self))
        bottom.addStretch(1)
        self.runBtn = PrimaryToolButton(FIF.PLAY, self)
        self.runBtn.setToolTip("立即執行")
        self.runBtn.setEnabled(any(s.enabled for s in chain.steps))
        self.runBtn.clicked.connect(lambda: self.runClicked.emit(self.chain))
        bottom.addWidget(self.runBtn)
        v.addLayout(bottom)
        self.clicked.connect(lambda: self.openClicked.emit(chain.name))


class IconTile(QFrame):
    """主題色淡底的圓角圖示方塊。"""

    def __init__(self, icon, size: int = 52, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.icon = IconWidget(icon, self)
        self.icon.setFixedSize(size // 2, size // 2)
        lay.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignCenter)
        qconfig.themeColorChanged.connect(lambda *_: self._restyle())
        qconfig.themeChanged.connect(lambda *_: self._restyle())
        self._restyle()

    def setIcon(self, icon) -> None:
        self.icon.setIcon(icon)

    def _restyle(self) -> None:
        c = themeColor()
        self.setStyleSheet(
            f"IconTile {{ background: rgba({c.red()},{c.green()},{c.blue()},{40 if isDarkTheme() else 28});"
            f" border-radius: 12px; }}"
        )


class StatusCard(CardWidget):
    """目前狀態：待命（附上次結果）／執行中（含進度）。"""

    stopClicked = Signal()
    detailClicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(104)
        h = QHBoxLayout(self)
        h.setContentsMargins(20, 16, 20, 16)
        h.setSpacing(16)
        self.tile = IconTile(FIF.GAME, parent=self)
        h.addWidget(self.tile)
        v = QVBoxLayout()
        v.setSpacing(6)
        self.title = SubtitleLabel("待命中", self)
        v.addWidget(self.title)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.badge = StatusBadge("idle", self)
        row.addWidget(self.badge)
        self.detail = muted_caption("沒有正在執行的任務", self)
        row.addWidget(self.detail, 1)
        v.addLayout(row)
        self.progress = ProgressBar(self)
        self.progress.setFixedHeight(4)
        self.progress.hide()
        v.addWidget(self.progress)
        h.addLayout(v, 1)
        self.detailBtn = PushButton(FIF.HISTORY, "查看執行", self)
        self.detailBtn.clicked.connect(self.detailClicked)
        self.stopBtn = PushButton(FIF.CANCEL, "停止", self)
        self.stopBtn.clicked.connect(self.stopClicked)
        self.stopBtn.hide()
        h.addWidget(self.detailBtn)
        h.addWidget(self.stopBtn)

    def set_idle(self, report: ChainReport | None) -> None:
        self.tile.setIcon(FIF.GAME)
        self.title.setText("待命中")
        self.progress.hide()
        self.stopBtn.hide()
        if report is None:
            self.badge.hide()
            self.detail.setText("沒有正在執行的任務")
            return
        failed = sum(r.status not in (StepStatus.SUCCESS, StepStatus.SKIPPED) for r in report.results)
        self.badge.setStatus("cancelled" if report.cancelled else ("success" if report.ok else "failed"))
        self.badge.show()
        took = fmt_duration((report.finished_at - report.started_at).total_seconds()) if report.finished_at else ""
        tail = f" · {failed} 個步驟未成功" if failed else ""
        self.detail.setText(f"上次執行「{report.chain}」· {report.finished_at:%H:%M} 結束 · 耗時 {took}{tail}")

    def set_running(self, chain: str) -> None:
        self.tile.setIcon(FIF.SYNC)
        self.title.setText(chain)
        self.badge.setStatus("running")
        self.badge.show()
        self.detail.setText("準備中…")
        self.progress.setValue(0)
        self.progress.show()
        self.stopBtn.show()

    def set_step(self, index: int, total: int, name: str) -> None:
        self.detail.setText(f"第 {index + 1}/{total} 步：{name}")
        self.progress.setValue(int(index / max(1, total) * 100))


class HomePage(SmoothScrollArea):
    runRequested = Signal(object)
    openChain = Signal(str)
    showRun = Signal()
    createChain = Signal()
    elevateRequested = Signal()

    def __init__(self, controller: AppController, parent=None):
        super().__init__(parent)
        self.setObjectName("homePage")
        self.controller = controller
        self._total = 0
        self.setWidgetResizable(True)
        self.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        body = QWidget()
        body.setStyleSheet("background: transparent;")
        self.v = QVBoxLayout(body)
        self.v.setContentsMargins(32, 28, 32, 32)
        self.v.setSpacing(8)
        self.setWidget(body)

        self.hello = TitleLabel("", body)
        self.v.addWidget(self.hello)
        self.date = muted_caption("", body)
        self.v.addWidget(self.date)
        self.v.addSpacing(12)

        self.adminBar = InfoBar(
            InfoBarIcon.WARNING, "未以系統管理員身分執行",
            "有任務鏈使用需要管理員權限的腳本，執行時會跳出 UAC 提示，且無法自動停止。",
            orient=Qt.Orientation.Horizontal, isClosable=False, duration=-1, parent=body,
        )
        elevate = PushButton("以管理員身分重新啟動", self.adminBar)
        elevate.clicked.connect(self.elevateRequested)
        self.adminBar.addWidget(elevate)
        self.adminBar.hide()
        self.v.addWidget(self.adminBar)

        self.status = StatusCard(body)
        self.status.stopClicked.connect(controller.request_stop)
        self.status.detailClicked.connect(self.showRun)
        self.v.addWidget(self.status)

        self.nextLabel = muted_caption("", body)
        self.nextLabel.setContentsMargins(4, 2, 0, 0)
        self.v.addWidget(self.nextLabel)
        self.scheduler = None

        self.v.addWidget(SectionHeader("快速執行", body))
        self.cardsHost = QWidget(body)
        self.flow = FlowLayout(self.cardsHost, needAni=False)
        self.flow.setContentsMargins(0, 0, 0, 0)
        self.flow.setHorizontalSpacing(12)
        self.flow.setVerticalSpacing(12)
        self.v.addWidget(self.cardsHost)
        self.empty = EmptyState(FIF.ROBOT, "還沒有任務鏈", "建立一條任務鏈，把要跑的腳本串起來", "建立任務鏈", body)
        self.empty.actionClicked.connect(self.createChain)
        self.v.addWidget(self.empty)
        self.v.addStretch(1)

        controller.chainsChanged.connect(self.reload)
        controller.runStarted.connect(self._on_started)
        controller.runEvent.connect(self._on_event)
        controller.runFinished.connect(self._on_finished)
        controller.historyChanged.connect(self.reload)
        self.reload()
        self._refresh_header()

    def set_scheduler(self, scheduler) -> None:
        self.scheduler = scheduler
        scheduler.schedulesChanged.connect(self._refresh_next)
        scheduler.queueChanged.connect(self._refresh_next)
        self._refresh_next()

    def _refresh_next(self) -> None:
        if self.scheduler is None:
            return
        from .schedule_page import fmt_relative, fmt_when

        now = datetime.now()
        if self.scheduler.paused:
            self.nextLabel.setText("⏸ 今日排程已暫停")
            return
        ups = self.scheduler.upcoming(1)
        if ups:
            dt, s = ups[0]
            self.nextLabel.setText(f"下一個排程：{fmt_when(dt, now)}　{s.chain}（{fmt_relative(dt, now)}）")
        else:
            self.nextLabel.setText("尚未設定排程")

    def _refresh_header(self) -> None:
        now = datetime.now()
        self.hello.setText(_greeting())
        self.date.setText(f"{now:%Y 年 %m 月 %d 日}　星期{WEEKDAYS[now.weekday()]}")

    def showEvent(self, e) -> None:
        self._refresh_header()
        self._refresh_next()
        super().showEvent(e)

    def reload(self) -> None:
        self.flow.takeAllWidgets()
        chains = self.controller.chains()
        try:
            from datetime import date as _date

            today = {d.chain: (d.last_status, d.runs) for d in self.controller.history.daily(1, _date.today())}
        except Exception:
            today = {}
        for c in chains:
            card = ChainCard(c, today.get(c.name), self.cardsHost)
            card.runBtn.setEnabled(card.runBtn.isEnabled() and not self.controller.running)
            card.runClicked.connect(self.runRequested)
            card.openClicked.connect(self.openChain)
            self.flow.addWidget(card)
        self.adminBar.setVisible(not is_admin() and any(self.controller.needs_admin(c) for c in chains))
        self.cardsHost.setVisible(bool(chains))
        self.empty.setVisible(not chains)
        if not self.controller.running:
            self.status.set_idle(self.controller.last_report)

    def _set_cards_enabled(self, enabled: bool) -> None:
        for card in self.cardsHost.findChildren(ChainCard):
            card.runBtn.setEnabled(enabled and any(s.enabled for s in card.chain.steps))

    def _on_started(self, name: str) -> None:
        self.status.set_running(name)
        self._set_cards_enabled(False)

    def _on_event(self, kind: str, data: dict) -> None:
        if kind == "chain_start":
            self._total = len(data["steps"])
        elif kind == "step_start":
            self.status.set_step(data["index"], self._total, data["name"])

    def _on_finished(self, report) -> None:
        self.status.set_idle(report)
        self._set_cards_enabled(True)
