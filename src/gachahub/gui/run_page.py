"""執行頁：目前任務鏈狀態、各步驟進度表、即時日誌。"""

from __future__ import annotations

import time
from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QHBoxLayout, QHeaderView, QTableWidgetItem, QVBoxLayout, QWidget
from qfluentwidgets import (
    CardWidget,
    FluentIcon as FIF,
    PlainTextEdit,
    PushButton,
    SubtitleLabel,
    TableWidget,
    TransparentToolButton,
)

from ..core.models import ChainReport, StepResult
from .controller import AppController
from .widgets import SectionHeader, StatusBadge, fmt_duration, mono_font, muted_caption

MAX_LOG_LINES = 5000


class RunPage(QWidget):
    def __init__(self, controller: AppController, parent=None):
        super().__init__(parent)
        self.setObjectName("runPage")
        self.controller = controller
        self._started = 0.0
        self._step_started: dict[int, float] = {}

        v = QVBoxLayout(self)
        v.setContentsMargins(32, 28, 32, 24)
        v.setSpacing(8)

        # 狀態卡
        card = CardWidget(self)
        h = QHBoxLayout(card)
        h.setContentsMargins(20, 14, 20, 14)
        info = QVBoxLayout()
        info.setSpacing(2)
        row = QHBoxLayout()
        self.title = SubtitleLabel("尚未執行", card)
        row.addWidget(self.title)
        row.addSpacing(8)
        self.badge = StatusBadge("idle", card)
        row.addWidget(self.badge)
        row.addStretch(1)
        info.addLayout(row)
        self.elapsed = muted_caption("從首頁或任務鏈頁開始執行", card)
        info.addWidget(self.elapsed)
        h.addLayout(info, 1)
        self.stopBtn = PushButton(FIF.CANCEL, "停止並還原", card)
        self.stopBtn.setToolTip("終止目前腳本，還原音量等系統狀態；不會執行關機等後置動作")
        self.stopBtn.clicked.connect(controller.request_stop)
        self.stopBtn.setEnabled(False)
        h.addWidget(self.stopBtn)
        v.addWidget(card)

        # 步驟表
        v.addWidget(SectionHeader("步驟", self))
        self.table = TableWidget(self)
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["步驟", "狀態", "嘗試", "耗時", "訊息"])
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(40)
        self.table.setEditTriggers(TableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(TableWidget.SelectionMode.NoSelection)
        self.table.setBorderVisible(True)
        self.table.setBorderRadius(8)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(1, 96)
        self.table.setMinimumHeight(140)
        v.addWidget(self.table, 2)

        # 日誌
        log_head = SectionHeader("日誌", self)
        clear = TransparentToolButton(FIF.DELETE, self)
        clear.setToolTip("清除畫面上的日誌")
        log_head.addWidget(clear)
        openDir = TransparentToolButton(FIF.FOLDER, self)
        openDir.setToolTip("開啟日誌資料夾")
        openDir.clicked.connect(self._open_logs)
        log_head.addWidget(openDir)
        v.addWidget(log_head)
        self.log = PlainTextEdit(self)
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(MAX_LOG_LINES)
        self.log.setFont(mono_font())
        self.log.setPlaceholderText("執行時的訊息會顯示在這裡")
        clear.clicked.connect(self.log.clear)
        v.addWidget(self.log, 3)

        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._tick)

        controller.runStarted.connect(self._on_started)
        controller.runEvent.connect(self._on_event)
        controller.runLog.connect(self.append_log)
        controller.runFinished.connect(self._on_finished)

    # --- 槽 ---

    def append_log(self, msg: str) -> None:
        self.log.appendPlainText(f"{datetime.now():%H:%M:%S}  {msg}")
        self.log.moveCursor(QTextCursor.MoveOperation.End)

    def _on_started(self, name: str) -> None:
        self.title.setText(name)
        self.badge.setStatus("running")
        self.stopBtn.setEnabled(True)
        self.table.setRowCount(0)
        self._started = time.monotonic()
        self._step_started.clear()
        self._tick()
        self.timer.start()
        if self.log.toPlainText():
            self.log.appendPlainText("")

    def _on_event(self, kind: str, data: dict) -> None:
        if kind == "chain_start":
            self.table.setRowCount(len(data["steps"]))
            for i, name in enumerate(data["steps"]):
                self._set_row(i, name, "pending", "", "", "")
        elif kind == "step_start":
            self._step_started[data["index"]] = time.monotonic()
            self._set_row(data["index"], data["name"], "running", "", "", "")
        elif kind == "step_end":
            r: StepResult = data["result"]
            took = (r.finished_at - r.started_at).total_seconds()
            attempts = str(r.attempts) if r.attempts else "—"
            took_text = fmt_duration(took) if r.attempts else "—"
            self._set_row(data["index"], r.step, r.status.value, attempts, took_text, r.message)

    def _on_finished(self, report: ChainReport | None) -> None:
        self.timer.stop()
        self.stopBtn.setEnabled(False)
        if report is None:
            self.badge.setStatus("failed")
            self.elapsed.setText("執行時發生錯誤，詳見日誌")
            return
        self.badge.setStatus("cancelled" if report.cancelled else ("success" if report.ok else "failed"))
        took = fmt_duration((report.finished_at - report.started_at).total_seconds())
        self.elapsed.setText(f"{report.finished_at:%H:%M:%S} 結束，總耗時 {took}")
        # 未執行到的步驟標為已略過
        for row in range(len(report.results), self.table.rowCount()):
            holder = self.table.cellWidget(row, 1)
            badge = holder.findChild(StatusBadge) if holder else None
            if badge:
                badge.setStatus("skipped")

    def _tick(self) -> None:
        self.elapsed.setText(f"已執行 {fmt_duration(time.monotonic() - self._started)}")

    # --- 工具 ---

    def _set_row(self, row: int, name: str, status: str, attempts: str, took: str, msg: str) -> None:
        for col, text in ((0, name), (2, attempts), (3, took), (4, msg)):
            item = QTableWidgetItem(text)
            item.setToolTip(text)
            self.table.setItem(row, col, item)
        holder = QWidget()
        hl = QHBoxLayout(holder)
        hl.setContentsMargins(8, 0, 8, 0)
        hl.addWidget(StatusBadge(status, holder), 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.table.setCellWidget(row, 1, holder)

    def _open_logs(self) -> None:
        import os

        self.controller.paths.logs.mkdir(parents=True, exist_ok=True)
        os.startfile(self.controller.paths.logs)
