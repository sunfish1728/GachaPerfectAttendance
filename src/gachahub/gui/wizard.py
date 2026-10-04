"""首次使用嚮導：掃描已安裝的腳本 → 選擇任務 → 建立任務鏈（與每日排程）。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTime
from PySide6.QtWidgets import QApplication, QFormLayout, QHBoxLayout, QStackedWidget, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CheckBox,
    ComboBox,
    FluentIcon as FIF,
    LineEdit,
    MessageBoxBase,
    PushButton,
    SimpleCardWidget,
    StrongBodyLabel,
    SubtitleLabel,
    TimePicker,
)

from ..core.models import HookSpec, TaskChain, TaskStep
from ..core.registry import BUILTIN
from ..core.schedule import Schedule, ScheduleKind
from .home_page import IconTile
from .widgets import muted_caption

# 各類腳本預設勾選的任務（找不到就用清單第一個）
PREFERRED_TASK = {"ok": "DailyRoutineTask", "onedragon": ""}


class _Found:
    def __init__(self, adapter_id: str, name: str, path: Path, task_param: str | None, task_label: str, options, default: str):
        self.adapter_id, self.name, self.path = adapter_id, name, path
        self.task_param, self.task_label, self.options, self.default = task_param, task_label, options, default


def scan(registry) -> list[_Found]:
    found: list[_Found] = []
    for aid, info in registry.infos.items():
        cls = BUILTIN.get(info.base)
        if not cls or not getattr(cls, "uses_install_dir", False) or aid == info.base:
            continue  # 只掃描具名的腳本定義（例如 ok-nte），不掃描通用基底
        adapter = registry.create(aid)
        try:
            paths = adapter.detect()
        except Exception:
            paths = []
        exe = info.defaults.get("exe") or info.defaults.get("launcher")
        for p in paths:
            if exe and not (Path(p) / exe).exists():
                continue  # 同一基底的其他腳本（例如 ok-ww）不算在這個定義
            try:
                options = adapter.list_tasks({"install_dir": str(p)})
            except Exception:
                options = []
            pref = PREFERRED_TASK.get(info.base)
            values = [o.value for o in options]
            default = pref if pref in values else (values[0] if values else "")
            found.append(_Found(aid, info.name, Path(p), cls.task_param, cls.task_label, options, default))
    return found


class _FoundRow(SimpleCardWidget):
    def __init__(self, f: _Found, parent=None):
        super().__init__(parent)
        self.f = f
        h = QHBoxLayout(self)
        h.setContentsMargins(14, 10, 14, 10)
        h.setSpacing(12)
        self.check = CheckBox("", self)
        self.check.setChecked(True)
        self.check.setFixedWidth(28)
        h.addWidget(self.check)
        v = QVBoxLayout()
        v.setSpacing(0)
        v.addWidget(StrongBodyLabel(f.name, self))
        v.addWidget(muted_caption(str(f.path), self))
        h.addLayout(v, 1)
        self.taskBox = None
        if f.task_param and f.options:
            self.taskBox = ComboBox(self)
            self.taskBox.setMinimumWidth(220)
            for o in f.options:
                self.taskBox.addItem(o.label)
            values = [o.value for o in f.options]
            if f.default in values:
                self.taskBox.setCurrentIndex(values.index(f.default))
            h.addWidget(muted_caption(f.task_label, self))
            h.addWidget(self.taskBox)
        self.check.toggled.connect(lambda on: self.taskBox and self.taskBox.setEnabled(on))

    def step(self) -> TaskStep | None:
        if not self.check.isChecked():
            return None
        params = {"install_dir": str(self.f.path)}
        if self.taskBox is not None:
            params[self.f.task_param] = self.f.options[self.taskBox.currentIndex()].value
        label = self.f.name
        if self.taskBox is not None and params[self.f.task_param]:
            label += "：" + self.taskBox.currentText().split("（")[0]
        return TaskStep(name=label, adapter=self.f.adapter_id, params=params, timeout=7200.0)


class SetupWizard(MessageBoxBase):
    def __init__(self, controller, scheduler, parent):
        super().__init__(parent)
        self.controller, self.scheduler = controller, scheduler
        self.created: str | None = None
        self.stack = QStackedWidget(self)
        self.viewLayout.addWidget(self.stack)
        self.widget.setMinimumWidth(640)

        # 第 1 頁：歡迎
        p1 = QWidget()
        v1 = QVBoxLayout(p1)
        v1.setSpacing(10)
        top = QHBoxLayout()
        top.addWidget(IconTile(FIF.GAME, 48, p1))
        top.addSpacing(8)
        tv = QVBoxLayout()
        tv.addWidget(SubtitleLabel("歡迎使用二游腳本集合站", p1))
        tv.addWidget(muted_caption("把各家自動化腳本串起來、定時執行，跑完自動還原與通知。", p1))
        top.addLayout(tv, 1)
        v1.addLayout(top)
        v1.addSpacing(6)
        for line in ("① 掃描電腦中已安裝的腳本（ok 異環、絕區零一條龍…）",
                     "② 選擇要跑的任務，建立一條「每日日常」任務鏈",
                     "③ 可選：設定每天自動執行的時間"):
            v1.addWidget(BodyLabel(line, p1))
        v1.addSpacing(6)
        v1.addWidget(muted_caption("之後都可以在「任務鏈」與「排程」頁修改。也可以略過嚮導、自己建立。", p1))
        v1.addStretch(1)
        self.stack.addWidget(p1)

        # 第 2 頁：掃描結果
        p2 = QWidget()
        self.v2 = QVBoxLayout(p2)
        self.v2.setSpacing(8)
        self.v2.addWidget(SubtitleLabel("找到的腳本", p2))
        self.scanHint = muted_caption("", p2)
        self.v2.addWidget(self.scanHint)
        self.rowsBox = QVBoxLayout()
        self.rowsBox.setSpacing(6)
        self.v2.addLayout(self.rowsBox)
        rescan = PushButton(FIF.SYNC, "重新掃描", p2)
        rescan.clicked.connect(self._scan)
        self.v2.addWidget(rescan, 0, Qt.AlignmentFlag.AlignLeft)
        self.v2.addStretch(1)
        self.stack.addWidget(p2)
        self.rows: list[_FoundRow] = []

        # 第 3 頁：選項
        p3 = QWidget()
        v3 = QVBoxLayout(p3)
        v3.addWidget(SubtitleLabel("任務鏈設定", p3))
        form = QFormLayout()
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(12)
        self.nameEdit = LineEdit(p3)
        self.nameEdit.setText(self._unique_name("每日日常"))
        form.addRow("名稱", self.nameEdit)
        self.muteBox = CheckBox("執行期間靜音，結束後還原", p3)
        self.muteBox.setChecked(True)
        form.addRow("執行前", self.muteBox)
        sched = QHBoxLayout()
        self.schedBox = CheckBox("每天自動執行，時間", p3)
        self.schedBox.setChecked(True)
        self.timePicker = TimePicker(p3)
        self.timePicker.setTime(QTime(4, 30))
        self.schedBox.toggled.connect(self.timePicker.setEnabled)
        sched.addWidget(self.schedBox)
        sched.addWidget(self.timePicker)
        sched.addStretch(1)
        form.addRow("排程", sched)
        v3.addLayout(form)
        v3.addWidget(muted_caption("建議時間設在體力接近回滿、且你通常不用電腦的時候；你在用電腦時會自動延後。", p3))
        v3.addStretch(1)
        self.stack.addWidget(p3)

        self.backBtn = PushButton("上一步", self.buttonGroup)
        self.backBtn.clicked.connect(self._back)
        self.buttonLayout.insertWidget(0, self.backBtn, 1, Qt.AlignmentFlag.AlignVCenter)
        self.cancelButton.setText("略過")
        self._sync_buttons()

    def _unique_name(self, base: str) -> str:
        names = {c.name for c in self.controller.chains()}
        name, n = base, 2
        while name in names:
            name, n = f"{base} {n}", n + 1
        return name

    def _sync_buttons(self) -> None:
        i = self.stack.currentIndex()
        self.backBtn.setVisible(i > 0)
        self.yesButton.setText({0: "開始掃描", 1: "下一步", 2: "完成"}[i])

    def _back(self) -> None:
        self.stack.setCurrentIndex(max(0, self.stack.currentIndex() - 1))
        self._sync_buttons()

    def _scan(self) -> None:
        while self.rowsBox.count():
            item = self.rowsBox.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        self.scanHint.setText("掃描中…")
        QApplication.processEvents()
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            found = scan(self.controller.registry)
        finally:
            QApplication.restoreOverrideCursor()
        self.rows = [_FoundRow(f, self) for f in found]
        for r in self.rows:
            self.rowsBox.addWidget(r)
        if found:
            self.scanHint.setText(f"找到 {len(found)} 個腳本。勾選要加入的，並選擇要跑的任務：")
        else:
            self.scanHint.setText("沒有在常見位置找到支援的腳本。可以略過嚮導，之後在「任務鏈」頁手動選擇安裝資料夾。")

    def validate(self) -> bool:
        """「下一步」：前兩頁換頁，最後一頁才真的建立並關閉。"""
        i = self.stack.currentIndex()
        if i == 0:
            self.stack.setCurrentIndex(1)
            self._sync_buttons()
            self._scan()
            return False
        if i == 1:
            if not any(r.check.isChecked() for r in self.rows):
                self.scanHint.setText("請至少勾選一個腳本，或按「略過」自己建立。")
                return False
            self.stack.setCurrentIndex(2)
            self._sync_buttons()
            return False
        name = self.nameEdit.text().strip() or self._unique_name("每日日常")
        if name in {c.name for c in self.controller.chains()}:
            name = self._unique_name(name)
        steps = [s for s in (r.step() for r in self.rows) if s is not None]
        chain = TaskChain(
            name=name, steps=steps,
            pre_hooks=[HookSpec(type="mute", params={"mode": "master"})] if self.muteBox.isChecked() else [],
        )
        self.controller.save_chain(chain)
        if self.schedBox.isChecked():
            t = self.timePicker.getTime()
            self.scheduler.save(Schedule(chain=name, kind=ScheduleKind.DAILY, time=f"{t.hour():02d}:{t.minute():02d}"))
        self.created = name
        return True
