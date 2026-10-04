"""排程開始前的倒數提示：右下角浮動、置頂，主視窗縮在托盤時也看得到。

到時間自動開始；使用者可立即開始、延後或略過這次。
"""

from __future__ import annotations

import math
import time

from PySide6.QtCore import QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import QApplication, QGraphicsDropShadowEffect, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    FluentIcon as FIF,
    PrimaryPushButton,
    ProgressBar,
    PushButton,
    StrongBodyLabel,
    TransparentPushButton,
    isDarkTheme,
)

from .home_page import IconTile
from .scheduler_service import PendingRun
from .widgets import muted_caption

WIDTH = 380


class _Card(QWidget):
    """圓角卡片背景（隨主題）。"""

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 10, 10)
        dark = isDarkTheme()
        p.fillPath(path, QColor(43, 43, 43) if dark else QColor(252, 252, 252))
        p.setPen(QColor(255, 255, 255, 24) if dark else QColor(0, 0, 0, 22))
        p.drawPath(path)


class CountdownToast(QWidget):
    startNow = Signal()
    postponeRequested = Signal(int)
    skipRequested = Signal()

    def __init__(self, item: PendingRun, seconds: int, postpone_minutes: int = 15):
        super().__init__(None)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)  # 不搶走遊戲或使用者目前視窗的焦點
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.total = max(1, seconds)
        self.deadline = time.monotonic() + self.total
        self._done = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        card = _Card(self)
        shadow = QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(0, 0, 0, 70))
        card.setGraphicsEffect(shadow)
        outer.addWidget(card)

        v = QVBoxLayout(card)
        v.setContentsMargins(18, 16, 18, 14)
        v.setSpacing(10)
        top = QHBoxLayout()
        top.setSpacing(12)
        top.addWidget(IconTile(FIF.STOP_WATCH, 40, card))
        info = QVBoxLayout()
        info.setSpacing(2)
        info.addWidget(StrongBodyLabel(f"即將開始：{item.schedule.chain}", card))
        planned = f"{item.planned:%H:%M} 的排程"
        if item.defers:
            planned += f"（已延後 {item.defers} 次）"
        self.caption = muted_caption(planned, card)
        info.addWidget(self.caption)
        top.addLayout(info, 1)
        v.addLayout(top)

        self.bar = ProgressBar(card)
        self.bar.setRange(0, 1000)
        self.bar.setValue(1000)
        v.addWidget(self.bar)
        self.remain = muted_caption("", card)
        v.addWidget(self.remain)

        btns = QHBoxLayout()
        btns.setSpacing(8)
        go = PrimaryPushButton(FIF.PLAY, "立即開始", card)
        go.clicked.connect(self._start)
        later = PushButton(f"延後 {postpone_minutes} 分", card)
        later.clicked.connect(lambda: self._finish(lambda: self.postponeRequested.emit(postpone_minutes)))
        skip = TransparentPushButton("略過這次", card)
        skip.clicked.connect(lambda: self._finish(self.skipRequested.emit))
        btns.addWidget(go)
        btns.addWidget(later)
        btns.addStretch(1)
        btns.addWidget(skip)
        v.addLayout(btns)

        self.setFixedWidth(WIDTH)
        self.adjustSize()
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self._tick)
        self._tick()

    def popup(self) -> None:
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            self.move(QPoint(geo.right() - self.width() + 4, geo.bottom() - self.height() + 4))
        self.show()
        self.timer.start()

    def _tick(self) -> None:
        left = self.deadline - time.monotonic()
        if left <= 0:
            self._start()
            return
        self.bar.setValue(int(left / self.total * 1000))
        self.remain.setText(f"{min(self.total, math.ceil(left))} 秒後自動開始")

    def _start(self) -> None:
        self._finish(self.startNow.emit)

    def _finish(self, action) -> None:
        if self._done:
            return
        self._done = True
        self.timer.stop()
        self.close()
        action()

    def dismiss(self) -> None:
        """由外部取消（例如任務已被其他方式啟動）。"""
        self._done = True
        self.timer.stop()
        self.close()
