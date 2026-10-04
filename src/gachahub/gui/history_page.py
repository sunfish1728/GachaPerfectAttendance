"""歷史頁：近 7 天統計、完成看板、執行紀錄與每步明細。"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QHeaderView, QTableWidgetItem, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    ComboBox,
    FluentIcon as FIF,
    MessageBoxBase,
    PushButton,
    SimpleCardWidget,
    SmoothScrollArea,
    SubtitleLabel,
    TableWidget,
    TitleLabel,
    TransparentToolButton,
    isDarkTheme,
    qconfig,
)

from .controller import AppController
from . import theme
from .widgets import STATUS_STYLE, SectionHeader, StatusBadge, fmt_duration, muted_caption

WEEK = "一二三四五六日"
TRIGGER_TEXT = {"manual": "手動", "schedule": "排程", "tray": "托盤"}
PAGE_SIZE = 30


def _status_of(run) -> str:
    return "cancelled" if run.cancelled else ("success" if run.ok else "failed")


class StatTile(SimpleCardWidget):
    def __init__(self, label: str, parent=None):
        super().__init__(parent)
        self.setFixedHeight(84)
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 12, 18, 12)
        v.setSpacing(2)
        v.addWidget(muted_caption(label, self))
        self.value = TitleLabel("—", self)
        self.value.setTextColor(theme.color("data", False), theme.color("data", True))
        v.addWidget(self.value)

    def set(self, text: str) -> None:
        self.value.setText(text)


class WeekBoard(QWidget):
    """完成看板：列 = 任務鏈，欄 = 近 7 天；方塊顏色表示當天最後一次結果，數字為次數。"""

    ROW_H = 34
    NAME_W = 150

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows: list[str] = []
        self.cells: dict[tuple[str, date], tuple[str, int, int]] = {}  # (chain, day) -> (status, runs, ok_runs)
        self.days: list[date] = []
        qconfig.themeChanged.connect(self.update)

    def set_data(self, daily, today: date) -> None:
        self.days = [today - timedelta(days=i) for i in range(6, -1, -1)]
        self.cells = {(d.chain, d.date): (d.last_status, d.runs, d.ok_runs) for d in daily}
        self.rows = sorted({d.chain for d in daily})
        self.setFixedHeight(self.ROW_H * (len(self.rows) + 1) + 8)
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(600, self.ROW_H * (len(self.rows) + 1) + 8)

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        text = theme.color("text")
        muted = theme.color("muted")
        empty = theme.color("track")
        font = QFont(self.font())
        font.setPointSize(9)
        p.setFont(font)
        col_w = max(56, (self.width() - self.NAME_W) / max(1, len(self.days)))
        today = self.days[-1] if self.days else None
        # 表頭
        for i, d in enumerate(self.days):
            x = self.NAME_W + i * col_w
            p.setPen(text if d == today else muted)
            label = "今天" if d == today else f"{d:%m/%d} {WEEK[d.weekday()]}"
            p.drawText(QRectF(x, 0, col_w, self.ROW_H), Qt.AlignmentFlag.AlignCenter, label)
        if not self.rows:
            p.setPen(muted)
            p.drawText(QRectF(0, self.ROW_H, self.width(), self.ROW_H), Qt.AlignmentFlag.AlignCenter, "近 7 天沒有執行紀錄")
            return
        for r, chain in enumerate(self.rows):
            y = (r + 1) * self.ROW_H
            p.setPen(text)
            name = p.fontMetrics().elidedText(chain, Qt.TextElideMode.ElideRight, self.NAME_W - 12)
            p.drawText(QRectF(0, y, self.NAME_W - 8, self.ROW_H), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, name)
            for i, d in enumerate(self.days):
                cx = self.NAME_W + i * col_w + col_w / 2
                cy = y + self.ROW_H / 2
                cell = self.cells.get((chain, d))
                if cell is None:
                    p.fillRect(QRectF(cx - 4, cy - 4, 8, 8), empty)
                    continue
                status, runs, ok_runs = cell
                _, token = STATUS_STYLE.get(status, STATUS_STYLE["idle"])
                p.fillRect(QRectF(cx - 10, cy - 9, 20, 18), theme.color(token))
                if runs > 1:
                    p.setPen(QPen(theme.color("canvas")))
                    p.drawText(QRectF(cx - 10, cy - 9, 20, 18), Qt.AlignmentFlag.AlignCenter, str(runs))
            p.fillRect(QRectF(0, y + self.ROW_H - 1, self.width(), 1), theme.color("line"))


class RunDetailDialog(MessageBoxBase):
    def __init__(self, controller: AppController, run, parent):
        super().__init__(parent)
        self.viewLayout.addWidget(SubtitleLabel(f"{run.chain}", self))
        info = f"{run.started_at:%Y/%m/%d %H:%M:%S} · {TRIGGER_TEXT.get(run.trigger, run.trigger)} · 耗時 {fmt_duration(run.duration_sec)} · {run.summary}"
        self.viewLayout.addWidget(muted_caption(info, self))
        table = TableWidget(self)
        table.setColumnCount(5)
        table.setHorizontalHeaderLabels(["步驟", "狀態", "嘗試", "耗時", "訊息"])
        table.verticalHeader().hide()
        table.verticalHeader().setDefaultSectionSize(40)
        table.setEditTriggers(TableWidget.EditTrigger.NoEditTriggers)
        table.setBorderVisible(True)
        table.setBorderRadius(0)
        steps = controller.history.steps(run.id)
        table.setRowCount(len(steps))
        for r, s in enumerate(steps):
            for c, txt in ((0, s.step), (2, str(s.attempts) if s.attempts else "—"),
                           (3, fmt_duration(s.duration_sec) if s.attempts else "—"), (4, s.message)):
                item = QTableWidgetItem(txt)
                item.setToolTip(txt)
                table.setItem(r, c, item)
            holder = QWidget()
            hl = QHBoxLayout(holder)
            hl.setContentsMargins(8, 0, 8, 0)
            hl.addWidget(StatusBadge(s.status, holder), 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            table.setCellWidget(r, 1, holder)
        hh = table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        table.setColumnWidth(1, 96)
        table.setMinimumHeight(min(360, 48 + 40 * max(1, len(steps))))
        self.viewLayout.addWidget(table)
        self.yesButton.setText("關閉")
        self.cancelButton.hide()
        self.widget.setMinimumWidth(720)


class HistoryPage(SmoothScrollArea):
    def __init__(self, controller: AppController, parent=None):
        super().__init__(parent)
        self.setObjectName("historyPage")
        self.controller = controller
        self._runs = []
        self._chain_filter: str | None = None
        self.setWidgetResizable(True)
        self.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        body = QWidget()
        body.setStyleSheet("background: transparent;")
        v = QVBoxLayout(body)
        v.setContentsMargins(32, 28, 32, 32)
        v.setSpacing(8)
        self.setWidget(body)

        head = QHBoxLayout()
        head.addWidget(TitleLabel("歷史", body))
        head.addStretch(1)
        shots = PushButton(FIF.PHOTO, "失敗截圖", body)
        shots.setToolTip("開啟失敗現場截圖資料夾")
        shots.clicked.connect(self._open_failures)
        head.addWidget(shots)
        v.addLayout(head)
        v.addWidget(muted_caption("近 7 天", body))

        tiles = QHBoxLayout()
        tiles.setSpacing(12)
        self.tRuns, self.tRate, self.tFail, self.tTime = (StatTile(t, body) for t in ("執行次數", "成功率", "失敗", "總執行時間"))
        for t in (self.tRuns, self.tRate, self.tFail, self.tTime):
            tiles.addWidget(t)
        v.addLayout(tiles)

        v.addWidget(SectionHeader("完成看板", body))
        boardCard = SimpleCardWidget(body)
        bl = QVBoxLayout(boardCard)
        bl.setContentsMargins(20, 10, 20, 12)
        self.board = WeekBoard(boardCard)
        bl.addWidget(self.board)
        legend = QHBoxLayout()
        legend.setSpacing(16)
        for key in ("success", "failed", "cancelled"):
            legend.addWidget(StatusBadge(key, boardCard))
        legend.addWidget(muted_caption("方塊為當天最後一次結果；數字為當天執行次數", boardCard))
        legend.addStretch(1)
        bl.addLayout(legend)
        v.addWidget(boardCard)

        rh = SectionHeader("執行紀錄", body)
        self.filterBox = ComboBox(body)
        self.filterBox.setMinimumWidth(160)
        self.filterBox.currentIndexChanged.connect(self._on_filter)
        rh.addWidget(self.filterBox)
        v.addWidget(rh)
        self.table = TableWidget(body)
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(["時間", "任務鏈", "來源", "結果", "耗時", "摘要"])
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(40)
        self.table.setEditTriggers(TableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(TableWidget.SelectionBehavior.SelectRows)
        self.table.setBorderVisible(True)
        self.table.setBorderRadius(0)
        hh = self.table.horizontalHeader()
        for c in (0, 1, 2, 4):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        hh.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(3, 96)
        self.table.cellDoubleClicked.connect(self._open_detail)
        v.addWidget(self.table)
        foot = QHBoxLayout()
        self.countLabel = muted_caption("", body)
        foot.addWidget(self.countLabel)
        foot.addStretch(1)
        self.moreBtn = PushButton("載入更多", body)
        self.moreBtn.clicked.connect(self._load_more)
        foot.addWidget(self.moreBtn)
        v.addLayout(foot)
        v.addStretch(1)

        controller.historyChanged.connect(self.reload)
        self.reload()

    def showEvent(self, e) -> None:
        self.reload()
        super().showEvent(e)

    def reload(self) -> None:
        h = self.controller.history
        now = datetime.now()
        since = (now - timedelta(days=6)).replace(hour=0, minute=0, second=0, microsecond=0)
        st = h.stats(since)
        runs = st.get("runs", 0)
        self.tRuns.set(str(runs))
        self.tRate.set(f"{st.get('ok_runs', 0) / runs * 100:.0f}%" if runs else "—")
        self.tFail.set(str(st.get("failed_runs", 0)))
        self.tTime.set(fmt_duration(st.get("total_duration_sec", 0)) if runs else "—")
        self.board.set_data(h.daily(7, now.date()), now.date())

        chains = h.chains()
        self.filterBox.blockSignals(True)
        self.filterBox.clear()
        self.filterBox.addItem("全部任務鏈")
        self.filterBox.addItems(chains)
        if self._chain_filter in chains:
            self.filterBox.setCurrentIndex(chains.index(self._chain_filter) + 1)
        else:
            self._chain_filter = None
        self.filterBox.blockSignals(False)
        self._runs = h.runs(limit=PAGE_SIZE, chain=self._chain_filter)
        self._render()

    def _on_filter(self, idx: int) -> None:
        self._chain_filter = None if idx <= 0 else self.filterBox.itemText(idx)
        self._runs = self.controller.history.runs(limit=PAGE_SIZE, chain=self._chain_filter)
        self._render()

    def _load_more(self) -> None:
        self._runs += self.controller.history.runs(limit=PAGE_SIZE, offset=len(self._runs), chain=self._chain_filter)
        self._render()

    def _render(self) -> None:
        now = datetime.now()
        self.table.setRowCount(len(self._runs))
        for r, run in enumerate(self._runs):
            when = run.started_at.strftime("%H:%M" if run.started_at.date() == now.date() else "%m/%d %H:%M")
            if run.started_at.date() == now.date():
                when = f"今天 {when}"
            for c, txt in ((0, when), (1, run.chain), (2, TRIGGER_TEXT.get(run.trigger, run.trigger)),
                           (4, fmt_duration(run.duration_sec)), (5, run.summary)):
                item = QTableWidgetItem(txt)
                item.setToolTip(txt)
                self.table.setItem(r, c, item)
            holder = QWidget()
            hl = QHBoxLayout(holder)
            hl.setContentsMargins(8, 0, 8, 0)
            hl.addWidget(StatusBadge(_status_of(run), holder), 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            self.table.setCellWidget(r, 3, holder)
        self.table.setFixedHeight(44 + 40 * max(3, min(len(self._runs), 12)))
        total = self.controller.history.count(chain=self._chain_filter)
        self.countLabel.setText(f"共 {total} 筆，顯示 {len(self._runs)} 筆；雙擊查看每步明細" if total else "還沒有執行紀錄")
        self.moreBtn.setVisible(len(self._runs) < total)

    def _open_detail(self, row: int, _col: int) -> None:
        if 0 <= row < len(self._runs):
            RunDetailDialog(self.controller, self._runs[row], self.window()).exec()

    def _open_failures(self) -> None:
        d = self.controller.paths.runtime / "failures"
        d.mkdir(parents=True, exist_ok=True)
        os.startfile(d)
