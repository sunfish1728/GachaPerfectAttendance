"""排程頁：接下來的執行、等待佇列、排程清單與編輯。"""

from __future__ import annotations

import sys
from datetime import datetime

from PySide6.QtCore import QDate, Qt, QTime
from PySide6.QtWidgets import QFormLayout, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    Action,
    BodyLabel,
    CalendarPicker,
    CardWidget,
    CheckBox,
    ComboBox,
    FluentIcon as FIF,
    IconWidget,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    MessageBox,
    MessageBoxBase,
    PillPushButton,
    PrimaryPushButton,
    PushButton,
    RoundMenu,
    SegmentedWidget,
    SimpleCardWidget,
    SmoothScrollArea,
    SpinBox,
    StrongBodyLabel,
    SubtitleLabel,
    SwitchButton,
    TimePicker,
    TitleLabel,
    TransparentToolButton,
)

from ..core.process import is_admin
from ..core.schedule import Schedule, ScheduleKind, next_fire
from .scheduler_service import SchedulerService
from .widgets import EmptyState, SectionHeader, muted_caption

WEEK = "一二三四五六日"
KIND_TEXT = {
    ScheduleKind.DAILY: "每天",
    ScheduleKind.WEEKLY: "每週",
    ScheduleKind.INTERVAL: "間隔",
    ScheduleKind.ONCE: "單次",
}


def describe(s: Schedule) -> str:
    if s.kind == ScheduleKind.DAILY:
        return "每天"
    if s.kind == ScheduleKind.WEEKLY:
        days = sorted(set(s.weekdays))
        if days == list(range(7)):
            return "每天"
        if days == list(range(5)):
            return "平日（週一至週五）"
        if days == [5, 6]:
            return "週末"
        return "每週" + "、".join(WEEK[d] for d in days) if days else "每週（未選日期）"
    if s.kind == ScheduleKind.INTERVAL:
        m = s.interval_minutes
        every = f"{m // 60} 小時" if m % 60 == 0 else f"{m} 分鐘"
        return f"每 {every}（從 {s.time} 起算）"
    return f"{s.date} 單次"


def fmt_when(dt: datetime, now: datetime) -> str:
    days = (dt.date() - now.date()).days
    if days == 0:
        day = "今天"
    elif days == 1:
        day = "明天"
    elif days == 2:
        day = "後天"
    else:
        day = f"{dt:%m/%d}（{WEEK[dt.weekday()]}）"
    return f"{day} {dt:%H:%M}"


def fmt_relative(dt: datetime, now: datetime) -> str:
    sec = int((dt - now).total_seconds())
    if sec < 60:
        return "即將開始"
    m = sec // 60
    if m < 60:
        return f"{m} 分鐘後"
    h, m = divmod(m, 60)
    if h < 24:
        return f"{h} 小時 {m} 分後" if m else f"{h} 小時後"
    return f"{h // 24} 天後"


class ScheduleDialog(MessageBoxBase):
    def __init__(self, chains: list[str], schedule: Schedule | None, parent: QWidget):
        super().__init__(parent)
        self.result_schedule: Schedule | None = None
        s = schedule or Schedule(chain=chains[0] if chains else "")
        self._orig = s

        self.viewLayout.addWidget(SubtitleLabel("編輯排程" if schedule else "新增排程", self))
        form = self.form = QFormLayout()
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(12)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        self.chainBox = ComboBox(self)
        self.chainBox.addItems(chains)
        if s.chain in chains:
            self.chainBox.setCurrentIndex(chains.index(s.chain))
        elif s.chain:
            self.chainBox.addItem(f"{s.chain}（已不存在）")
            self.chainBox.setCurrentIndex(self.chainBox.count() - 1)
        self._chains = chains
        form.addRow("任務鏈", self.chainBox)

        self.kindSeg = SegmentedWidget(self)
        self._kinds = [ScheduleKind.DAILY, ScheduleKind.WEEKLY, ScheduleKind.INTERVAL, ScheduleKind.ONCE]
        for k in self._kinds:
            self.kindSeg.addItem(k.value, KIND_TEXT[k], onClick=lambda *_: self._sync_kind())
        self.kindSeg.setCurrentItem(s.kind.value)
        form.addRow("頻率", self.kindSeg)

        self.timePicker = TimePicker(self)
        h, m = (int(x) for x in s.time.split(":")) if ":" in s.time else (4, 30)
        self.timePicker.setTime(QTime(h, m))
        form.addRow("時間", self.timePicker)

        self.weekRow = QWidget(self)
        wr = QHBoxLayout(self.weekRow)
        wr.setContentsMargins(0, 0, 0, 0)
        wr.setSpacing(6)
        self.dayBtns: list[PillPushButton] = []
        for i, ch in enumerate(WEEK):
            b = PillPushButton(ch, self.weekRow)
            b.setCheckable(True)
            b.setChecked(i in s.weekdays)
            b.setFixedWidth(40)
            wr.addWidget(b)
            self.dayBtns.append(b)
        wr.addStretch(1)
        form.addRow("星期", self.weekRow)

        self.datePicker = CalendarPicker(self)
        if s.date:
            self.datePicker.setDate(QDate.fromString(s.date, "yyyy-MM-dd"))
        else:
            self.datePicker.setDate(QDate.currentDate())
        form.addRow("日期", self.datePicker)

        self.intervalBox = SpinBox(self)
        self.intervalBox.setRange(10, 24 * 60)
        self.intervalBox.setSingleStep(30)
        self.intervalBox.setSuffix(" 分鐘")
        self.intervalBox.setValue(s.interval_minutes)
        form.addRow("間隔", self.intervalBox)

        self.catchBox = SpinBox(self)
        self.catchBox.setRange(0, 24 * 60)
        self.catchBox.setSingleStep(30)
        self.catchBox.setSuffix(" 分鐘內")
        self.catchBox.setValue(s.catch_up_minutes)
        form.addRow("錯過補跑", self.catchBox)

        self.wakeBox = CheckBox("喚醒電腦並自動啟動本程式", self)
        self.wakeBox.setChecked(s.wake_computer)
        form.addRow("", self.wakeBox)
        self.wakeHint = muted_caption(
            "透過 Windows 工作排程器在排程前 3 分鐘喚醒電腦（需要以系統管理員身分執行本程式來建立，"
            "且電源選項需允許喚醒計時器）。", self)
        self.wakeHint.setWordWrap(True)
        form.addRow("", self.wakeHint)

        self.noteEdit = LineEdit(self)
        self.noteEdit.setPlaceholderText("備註（選填）")
        self.noteEdit.setText(s.note)
        form.addRow("備註", self.noteEdit)

        self.viewLayout.addLayout(form)
        self.preview = muted_caption("", self)
        self.preview.setTextColor("#005FB8", "#60CDFF")
        self.viewLayout.addWidget(self.preview)
        self.errorLabel = muted_caption("", self)
        self.errorLabel.setTextColor("#C42B1C", "#FF99A4")
        self.errorLabel.hide()
        self.viewLayout.addWidget(self.errorLabel)

        for w in (self.timePicker,):
            w.timeChanged.connect(lambda *_: self._update_preview())
        for b in self.dayBtns:
            b.toggled.connect(lambda *_: self._update_preview())
        self.datePicker.dateChanged.connect(lambda *_: self._update_preview())
        self.intervalBox.valueChanged.connect(lambda *_: self._update_preview())

        self.yesButton.setText("確定")
        self.cancelButton.setText("取消")
        self.widget.setMinimumWidth(520)
        self._sync_kind()

    def _kind(self) -> ScheduleKind:
        return ScheduleKind(self.kindSeg.currentRouteKey())

    def _sync_kind(self) -> None:
        k = self._kind()
        self.form.setRowVisible(self.weekRow, k == ScheduleKind.WEEKLY)
        self.form.setRowVisible(self.datePicker, k == ScheduleKind.ONCE)
        self.form.setRowVisible(self.intervalBox, k == ScheduleKind.INTERVAL)
        label = self.form.labelForField(self.timePicker)
        if label is not None:
            label.setText("起算時間" if k == ScheduleKind.INTERVAL else "時間")
        self._update_preview()

    def _build(self) -> Schedule:
        t = self.timePicker.getTime()
        d = self.datePicker.getDate()
        idx = self.chainBox.currentIndex()
        chain = self._chains[idx] if idx < len(self._chains) else self._orig.chain
        return self._orig.model_copy(update=dict(
            chain=chain,
            kind=self._kind(),
            time=f"{t.hour():02d}:{t.minute():02d}",
            weekdays=[i for i, b in enumerate(self.dayBtns) if b.isChecked()],
            date=d.toString("yyyy-MM-dd") if d.isValid() else None,
            interval_minutes=self.intervalBox.value(),
            catch_up_minutes=self.catchBox.value(),
            wake_computer=self.wakeBox.isChecked(),
            note=self.noteEdit.text().strip(),
        ))

    def _update_preview(self) -> None:
        try:
            s = self._build()
            nxt = next_fire(s.model_copy(update={"enabled": True}), datetime.now())
        except Exception:
            nxt = None
        now = datetime.now()
        self.preview.setText(f"下一次：{fmt_when(nxt, now)}（{fmt_relative(nxt, now)}）" if nxt else "依目前設定不會再觸發")

    def validate(self) -> bool:
        if not self._chains:
            self.errorLabel.setText("請先建立任務鏈")
            self.errorLabel.show()
            return False
        s = self._build()
        if s.kind == ScheduleKind.WEEKLY and not s.weekdays:
            self.errorLabel.setText("請至少選擇一天")
            self.errorLabel.show()
            return False
        self.result_schedule = s
        return True


class ScheduleCard(CardWidget):
    def __init__(self, s: Schedule, service: SchedulerService, page: "SchedulePage"):
        super().__init__(page)
        self.s, self.service, self.page = s, service, page
        self.setFixedHeight(76)
        h = QHBoxLayout(self)
        h.setContentsMargins(20, 10, 12, 10)
        h.setSpacing(16)

        tl = TitleLabel(s.time if s.kind != ScheduleKind.INTERVAL else f"↻ {s.time}", self)
        tl.setFixedWidth(110)
        if not s.enabled:
            tl.setTextColor("#8A8A8A", "#7A7A7A")
        h.addWidget(tl)

        v = QVBoxLayout()
        v.setSpacing(2)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(StrongBodyLabel(s.chain, self))
        if s.wake_computer:
            ic = IconWidget(FIF.POWER_BUTTON, self)
            ic.setFixedSize(14, 14)
            ic.setToolTip("會喚醒電腦")
            row.addWidget(ic)
        row.addStretch(1)
        v.addLayout(row)
        now = datetime.now()
        nxt = next_fire(s, now)
        detail = describe(s)
        if s.note:
            detail += f" · {s.note}"
        if not s.enabled:
            detail += " · 已停用"
        else:
            detail += f" · 下次 {fmt_when(nxt, now)}" if nxt else " · 不會再觸發"
        v.addWidget(muted_caption(detail, self))
        h.addLayout(v, 1)

        sw = SwitchButton(self)
        sw.setOnText("")
        sw.setOffText("")
        sw.setChecked(s.enabled)
        sw.checkedChanged.connect(self._toggle)
        h.addWidget(sw)
        more = TransparentToolButton(FIF.MORE, self)
        more.clicked.connect(lambda: self._menu(more))
        h.addWidget(more)
        self.clicked.connect(lambda: page.edit(s))

    def _toggle(self, on: bool) -> None:
        self.service.save(self.s.model_copy(update={"enabled": on}))

    def _menu(self, anchor) -> None:
        m = RoundMenu(parent=self)
        m.addAction(Action(FIF.PLAY, "立即執行", triggered=lambda: self.service.run_now(self.s)))
        m.addAction(Action(FIF.EDIT, "編輯", triggered=lambda: self.page.edit(self.s)))
        m.addAction(Action(FIF.DELETE, "刪除", triggered=lambda: self.page.delete(self.s)))
        m.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))


class SchedulePage(SmoothScrollArea):
    def __init__(self, service: SchedulerService, parent=None):
        super().__init__(parent)
        self.setObjectName("schedulePage")
        self.service = service
        self.setWidgetResizable(True)
        self.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        body = QWidget()
        body.setStyleSheet("background: transparent;")
        self.v = QVBoxLayout(body)
        self.v.setContentsMargins(32, 28, 32, 32)
        self.v.setSpacing(8)
        self.setWidget(body)

        head = QHBoxLayout()
        head.addWidget(TitleLabel("排程", body))
        head.addStretch(1)
        self.pauseBtn = PushButton(FIF.PAUSE, "今日暫停", body)
        self.pauseBtn.clicked.connect(self._toggle_pause)
        add = PrimaryPushButton(FIF.ADD, "新增排程", body)
        add.clicked.connect(lambda: self.edit(None))
        head.addWidget(self.pauseBtn)
        head.addWidget(add)
        self.v.addLayout(head)
        self.status = muted_caption("", body)
        self.v.addWidget(self.status)

        self.v.addWidget(SectionHeader("接下來", body))
        self.upcomingCard = SimpleCardWidget(body)
        self.upcomingBox = QVBoxLayout(self.upcomingCard)
        self.upcomingBox.setContentsMargins(20, 10, 20, 10)
        self.upcomingBox.setSpacing(6)
        self.v.addWidget(self.upcomingCard)

        self.v.addWidget(SectionHeader("全部排程", body))
        self.listBox = QVBoxLayout()
        self.listBox.setSpacing(6)
        self.v.addLayout(self.listBox)
        self.v.addStretch(1)

        service.schedulesChanged.connect(self.reload)
        service.queueChanged.connect(self.reload)
        service.controller.chainsChanged.connect(self.reload)
        self.reload()

    def showEvent(self, e) -> None:
        self.reload()  # 相對時間會變，顯示時刷新
        super().showEvent(e)

    @staticmethod
    def _clear(layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w:
                w.hide()
                w.deleteLater()
            elif item.layout():
                SchedulePage._clear(item.layout())

    def reload(self) -> None:
        now = datetime.now()
        svc = self.service
        # 狀態列：暫停、等待佇列
        parts = []
        if svc.paused:
            parts.append(f"今日已暫停，{svc.paused_until:%m/%d %H:%M} 恢復")
        if svc.counting:
            parts.append(f"倒數中：{svc.counting.schedule.chain}")
        for p in svc.queue:
            if p is svc.counting:
                continue
            when = "等待目前任務結束" if p.not_before <= now else f"延後至 {p.not_before:%H:%M}"
            parts.append(f"等待中：{p.schedule.chain}（{when}{'，' + p.reason if p.reason else ''}）")
        self.status.setText("　".join(parts) if parts else "排程會在本程式執行時自動觸發；縮到托盤也會繼續。")
        self.pauseBtn.setText("恢復排程" if svc.paused else "今日暫停")
        self.pauseBtn.setIcon(FIF.PLAY if svc.paused else FIF.PAUSE)

        self._clear(self.upcomingBox)
        ups = svc.upcoming(5)
        if not ups:
            self.upcomingBox.addWidget(muted_caption("沒有即將執行的排程", self.upcomingCard))
        for dt, s in ups:
            row = QHBoxLayout()
            when = BodyLabel(fmt_when(dt, now), self.upcomingCard)
            when.setFixedWidth(150)
            row.addWidget(when)
            row.addWidget(StrongBodyLabel(s.chain, self.upcomingCard), 1)
            row.addWidget(muted_caption(fmt_relative(dt, now), self.upcomingCard))
            self.upcomingBox.addLayout(row)

        self._clear(self.listBox)
        schedules = svc.schedules()
        if not schedules:
            empty = EmptyState(FIF.CALENDAR, "還沒有排程", "排程會在指定時間自動執行任務鏈，例如每天 04:30 清體力", "新增排程", self)
            empty.actionClicked.connect(lambda: self.edit(None))
            self.listBox.addWidget(empty)
        for s in sorted(schedules, key=lambda x: (not x.enabled, x.time)):
            self.listBox.addWidget(ScheduleCard(s, svc, self))

    def _toggle_pause(self) -> None:
        if self.service.paused:
            self.service.resume()
        else:
            self.service.pause_today()

    def edit(self, s: Schedule | None) -> None:
        chains = [c.name for c in self.service.controller.chains()]
        if not chains:
            InfoBar.warning("無法新增排程", "請先到「任務鏈」頁建立任務鏈", parent=self.window(), position=InfoBarPosition.TOP)
            return
        dlg = ScheduleDialog(chains, s, self.window())
        if dlg.exec() and dlg.result_schedule:
            new = dlg.result_schedule
            self.service.save(new)
            if new.wake_computer or (s and s.wake_computer):
                self.window().sync_wake_tasks()

    def delete(self, s: Schedule) -> None:
        box = MessageBox("刪除排程", f"確定刪除「{s.chain}」在 {s.time} 的排程？", self.window())
        box.yesButton.setText("刪除")
        box.cancelButton.setText("取消")
        if box.exec():
            self.service.delete(s.id)
            if s.wake_computer:
                self.window().sync_wake_tasks()
