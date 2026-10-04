"""品牌裝飾：側欄頂端字標＋階梯色塊、首頁頂端的控制台標記列。"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget
from qfluentwidgets import NavigationWidget, qconfig

from . import theme

APP_NAME = "二遊全勤君"


def paint_steps(p: QPainter, x: float, bottom: float, width: float, low: float, high: float) -> None:
    """六段硬邊實色、由左往右逐段升高的階梯色塊。"""
    n = len(theme.BRAND_STEPS)
    w = width / n
    for i, c in enumerate(theme.BRAND_STEPS):
        h = low + (high - low) * i / (n - 1)
        p.fillRect(QRectF(x + i * w, bottom - h, w + 0.5, h), QColor(c))


class BrandWidget(NavigationWidget):
    """側欄頂端：鏽橘字標、階梯色塊與分隔線。"""

    HEIGHT = 70

    def __init__(self, parent=None):
        super().__init__(isSelectable=False, parent=parent)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        qconfig.themeChanged.connect(self.update)
        self.setCompacted(False)

    def setCompacted(self, isCompacted: bool) -> None:
        self.isCompacted = isCompacted
        self.setFixedSize(40 if isCompacted else self.EXPAND_WIDTH, self.HEIGHT)
        self.update()

    def mousePressEvent(self, e) -> None:
        e.ignore()

    def mouseReleaseEvent(self, e) -> None:
        e.ignore()

    def enterEvent(self, e) -> None:
        pass

    def leaveEvent(self, e) -> None:
        pass

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        w = self.width()
        if self.isCompacted:
            paint_steps(p, 6, self.HEIGHT - 14, w - 12, 3, 12)
        else:
            p.setFont(theme.ui_font(20, QFont.Weight.Bold))
            p.setPen(theme.color("action"))
            p.drawText(QRectF(12, 6, w - 12, 30), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, APP_NAME)
            f = theme.ui_font(9, QFont.Weight.DemiBold)
            f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.6)
            p.setFont(f)
            p.setPen(theme.color("muted"))
            p.drawText(QRectF(13, 34, w - 12, 12), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       "GACHA PERFECT ATTENDANCE")
            paint_steps(p, 12, self.HEIGHT - 9, min(132, w - 24), 3, 10)
        p.fillRect(QRectF(0, self.HEIGHT - 1, w, 1), theme.color("border_muted"))


class ConsoleStrip(QWidget):
    """首頁頂端：大寫技術標記，右側長水平色線在末端上下分岔。"""

    def __init__(self, labels: list[str], parent=None):
        super().__init__(parent)
        self.labels = labels
        self.setFixedHeight(26)
        qconfig.themeChanged.connect(self.update)

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        f = theme.ui_font(11, QFont.Weight.DemiBold)
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 2.0)
        p.setFont(f)
        x = 0.0
        mid = self.height() / 2
        for i, text in enumerate(self.labels):
            p.setPen(theme.color("action" if i == 0 else "muted"))
            width = p.fontMetrics().horizontalAdvance(text)
            p.drawText(QRectF(x, 0, width + 4, self.height()), Qt.AlignmentFlag.AlignVCenter, text)
            x += width + 10
            if i < len(self.labels) - 1:
                p.fillRect(QRectF(x, mid - 2, 4, 4), theme.color("border_muted"))
                x += 14
        start = x + 12
        end = self.width()
        if end - start < 60:
            return
        lines = (("action", -6, -1), ("chart2", -2, 1), ("text", 2, 1), ("border_muted", 6, -1))
        bend = end - 46
        for name, offset, direction in lines:
            pen = QPen(theme.color(name), 1.6)
            pen.setCapStyle(Qt.PenCapStyle.FlatCap)
            p.setPen(pen)
            y = mid + offset
            path = QPainterPath(QPointF(start, y))
            path.lineTo(QPointF(bend + offset * 2, y))
            target = 0 if direction < 0 else self.height()
            path.cubicTo(QPointF(bend + offset * 2 + 18, y), QPointF(end - 18, target),
                         QPointF(end, target))
            p.drawPath(path)
