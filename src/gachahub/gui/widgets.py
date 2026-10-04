"""共用小元件：狀態標籤、區段標題、空狀態、任務鏈摘要文字。"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    FluentIcon as FIF,
    IconWidget,
    PrimaryPushButton,
    StrongBodyLabel,
    isDarkTheme,
    qconfig,
)

from ..core.models import FailPolicy, StepStatus, TaskChain
from . import theme

# 狀態：(文字, 主題色票名稱)
STATUS_STYLE: dict[str, tuple[str, str]] = {
    "success": ("成功", "success"),
    "failed": ("失敗", "error"),
    "timeout": ("逾時", "warning"),
    "cancelled": ("已取消", "muted"),
    "skipped": ("已略過", "muted"),
    "running": ("執行中", "running"),
    "pending": ("等待中", "muted"),
    "idle": ("待命中", "muted"),
}

POLICY_TEXT = {
    FailPolicy.SKIP: "失敗時跳過",
    FailPolicy.RETRY: "失敗時重試",
    FailPolicy.ABORT: "失敗時中止",
}

POWER_TEXT = {"none": "無", "shutdown": "關機", "sleep": "睡眠", "hibernate": "休眠"}


class StatusBadge(QLabel):
    """方角線框狀態標籤，顏色隨主題切換。"""

    def __init__(self, status: str = "idle", parent: QWidget | None = None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumWidth(56)
        self.setFixedHeight(22)
        self._status = status
        qconfig.themeChanged.connect(lambda *_: self.setStatus(self._status))
        self.setStatus(status)

    def setStatus(self, status: str | StepStatus) -> None:
        status = status.value if isinstance(status, StepStatus) else status
        self._status = status
        text, token = STATUS_STYLE.get(status, STATUS_STYLE["idle"])
        c = theme.css(token)
        self.setText(text)
        self.setStyleSheet(
            f"QLabel {{ color: {c}; border: 1px solid {c}; border-left: 4px solid {c}; border-radius: 0px;"
            f" padding: 0px 8px; font-size: 12px; background: transparent; }}"
        )


class NoticeBar(QFrame):
    """扁平橫向提示：左側色條＋標題、可換行說明，右側操作按鈕。"""

    def __init__(self, title: str, text: str, level: str = "warning", parent: QWidget | None = None):
        super().__init__(parent)
        self.level = level
        h = QHBoxLayout(self)
        h.setContentsMargins(16, 10, 12, 10)
        h.setSpacing(14)
        v = QVBoxLayout()
        v.setSpacing(2)
        self.title = StrongBodyLabel(title, self)
        v.addWidget(self.title)
        self.text = muted_caption(text, self)
        self.text.setWordWrap(True)
        v.addWidget(self.text)
        h.addLayout(v, 1)
        self.actions = QHBoxLayout()
        h.addLayout(self.actions)
        qconfig.themeChanged.connect(lambda *_: self._restyle())
        self._restyle()

    def addWidget(self, w: QWidget) -> None:
        self.actions.addWidget(w)

    def _restyle(self) -> None:
        self.setStyleSheet(
            f"NoticeBar {{ background: {theme.css('inset')}; border: 1px solid {theme.css('border_muted')};"
            f" border-left: 4px solid {theme.css(self.level)}; border-radius: 0px; }}"
        )


class SectionHeader(QWidget):
    """區段標題，右側可放按鈕。"""

    def __init__(self, title: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.hbox = QHBoxLayout(self)
        self.hbox.setContentsMargins(2, 12, 0, 4)
        self.label = StrongBodyLabel(title, self)
        self.hbox.addWidget(self.label)
        self.hbox.addStretch(1)

    def addWidget(self, w: QWidget) -> None:
        self.hbox.addWidget(w)


class EmptyState(QWidget):
    """空狀態：圖示、說明與行動按鈕。"""

    actionClicked = Signal()

    def __init__(self, icon, title: str, hint: str, action: str | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.setSpacing(8)
        ic = IconWidget(icon, self)
        ic.setFixedSize(48, 48)
        v.addWidget(ic, 0, Qt.AlignmentFlag.AlignHCenter)
        v.addSpacing(4)
        t = StrongBodyLabel(title, self)
        v.addWidget(t, 0, Qt.AlignmentFlag.AlignHCenter)
        h = CaptionLabel(hint, self)
        h.setTextColor(theme.color("muted", False), theme.color("muted", True))
        v.addWidget(h, 0, Qt.AlignmentFlag.AlignHCenter)
        if action:
            v.addSpacing(8)
            btn = PrimaryPushButton(FIF.ADD, action, self)
            btn.clicked.connect(self.actionClicked)
            v.addWidget(btn, 0, Qt.AlignmentFlag.AlignHCenter)


def muted_caption(text: str, parent: QWidget | None = None) -> CaptionLabel:
    lbl = CaptionLabel(text, parent)
    lbl.setTextColor(theme.color("muted", False), theme.color("muted", True))
    return lbl


def chain_summary(chain: TaskChain) -> str:
    """例：「3 個步驟 · 靜音 · 結束後關機」"""
    parts = [f"{sum(s.enabled for s in chain.steps)} 個步驟"]
    for h in chain.pre_hooks:
        if not h.enabled:
            continue
        if h.type == "mute":
            parts.append("靜音")
        elif h.type == "kill_processes" and h.params.get("names"):
            parts.append("先關閉程式")
    for h in chain.post_actions:
        if h.enabled and h.type == "power" and h.params.get("action", "none") != "none":
            parts.append(f"結束後{POWER_TEXT.get(h.params['action'], h.params['action'])}")
    return " · ".join(parts)


def fmt_duration(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h} 小時 {m} 分"
    if m:
        return f"{m} 分 {s} 秒"
    return f"{s} 秒"


def mono_font() -> QFont:
    f = QFont()
    f.setFamilies(["Cascadia Mono", "Consolas", "Microsoft JhengHei UI"])
    f.setStyleHint(QFont.StyleHint.Monospace)
    f.setPointSize(10)
    return f


def body(text: str, parent: QWidget | None = None) -> BodyLabel:
    return BodyLabel(text, parent)
