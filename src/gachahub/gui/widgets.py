"""共用小元件：狀態標籤、區段標題、空狀態、任務鏈摘要文字。"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget
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

# 狀態色：(淺色主題, 深色主題)
STATUS_STYLE: dict[str, tuple[str, str, str]] = {
    # key: (文字, 淺色, 深色)
    "success": ("成功", "#0F7B0F", "#6CCB5F"),
    "failed": ("失敗", "#C42B1C", "#FF99A4"),
    "timeout": ("逾時", "#9D5D00", "#FCE100"),
    "cancelled": ("已取消", "#5D5D5D", "#A0A0A0"),
    "skipped": ("已略過", "#5D5D5D", "#A0A0A0"),
    "running": ("執行中", "#005FB8", "#60CDFF"),
    "pending": ("等待中", "#8A8A8A", "#7A7A7A"),
    "idle": ("待命中", "#5D5D5D", "#A0A0A0"),
}

POLICY_TEXT = {
    FailPolicy.SKIP: "失敗時跳過",
    FailPolicy.RETRY: "失敗時重試",
    FailPolicy.ABORT: "失敗時中止",
}

POWER_TEXT = {"none": "無", "shutdown": "關機", "sleep": "睡眠", "hibernate": "休眠"}


class StatusBadge(QLabel):
    """圓角膠囊狀態標籤，顏色隨主題切換。"""

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
        text, light, dark = STATUS_STYLE.get(status, STATUS_STYLE["idle"])
        color = dark if isDarkTheme() else light
        self.setText(text)
        self.setStyleSheet(
            f"QLabel {{ color: {color}; border: 1px solid {color}; border-radius: 10px;"
            f" padding: 0px 10px; font-size: 12px; background: transparent; }}"
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
        h.setTextColor("#606060", "#A0A0A0")
        v.addWidget(h, 0, Qt.AlignmentFlag.AlignHCenter)
        if action:
            v.addSpacing(8)
            btn = PrimaryPushButton(FIF.ADD, action, self)
            btn.clicked.connect(self.actionClicked)
            v.addWidget(btn, 0, Qt.AlignmentFlag.AlignHCenter)


def muted_caption(text: str, parent: QWidget | None = None) -> CaptionLabel:
    lbl = CaptionLabel(text, parent)
    lbl.setTextColor("#606060", "#9A9A9A")
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
