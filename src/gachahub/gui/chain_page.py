"""任務鏈頁：左側清單，右側編輯（步驟、執行前、執行後）。"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QListWidgetItem, QStackedWidget, QVBoxLayout, QWidget
from qfluentwidgets import (
    Action,
    BodyLabel,
    CardWidget,
    ComboBox,
    FluentIcon as FIF,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    ListWidget,
    MessageBox,
    PrimaryPushButton,
    PushButton,
    RoundMenu,
    SimpleCardWidget,
    SmoothScrollArea,
    StrongBodyLabel,
    SwitchButton,
    TransparentToolButton,
)

from ..core.models import HookSpec, TaskChain, TaskStep
from ..core.registry import BUILTIN
from .controller import AppController
from .step_dialog import StepDialog
from .widgets import POLICY_TEXT, EmptyState, SectionHeader, muted_caption


def _hook(hooks: list[HookSpec], type_: str) -> HookSpec | None:
    return next((h for h in hooks if h.type == type_), None)


def _names(text: str) -> list[str]:
    return [n.strip() for n in text.replace("，", ",").split(",") if n.strip()]


class StepCard(CardWidget):
    """單一步驟列：序號、啟用開關、名稱與摘要、操作按鈕。"""

    toggled = Signal(bool)
    editRequested = Signal()
    moveRequested = Signal(int)
    deleteRequested = Signal()

    def __init__(self, index: int, step: TaskStep, adapter_name: str, is_last: bool, task_text: str = "", parent=None):
        super().__init__(parent)
        self.setFixedHeight(68)
        h = QHBoxLayout(self)
        h.setContentsMargins(16, 8, 8, 8)
        h.setSpacing(12)

        num = StrongBodyLabel(str(index + 1), self)
        num.setFixedWidth(22)
        num.setAlignment(Qt.AlignmentFlag.AlignCenter)
        h.addWidget(num)

        sw = SwitchButton(self)
        sw.setOnText("")
        sw.setOffText("")
        sw.setChecked(step.enabled)
        sw.checkedChanged.connect(self.toggled)
        h.addWidget(sw)

        text = QVBoxLayout()
        text.setSpacing(0)
        title = BodyLabel(step.name, self)
        if not step.enabled:
            title.setTextColor("#8A8A8A", "#7A7A7A")
        detail = f"{adapter_name}{f'：{task_text}' if task_text else ''} · 逾時 {round(step.timeout / 60)} 分鐘 · {POLICY_TEXT[step.on_fail]}"
        if step.on_fail.value == "retry":
            detail += f" {step.retries} 次"
        text.addWidget(title)
        text.addWidget(muted_caption(detail, self))
        h.addLayout(text, 1)

        for icon, tip, cb, enabled in (
            (FIF.UP, "上移", lambda: self.moveRequested.emit(-1), index > 0),
            (FIF.DOWN, "下移", lambda: self.moveRequested.emit(1), not is_last),
            (FIF.EDIT, "編輯", self.editRequested.emit, True),
            (FIF.DELETE, "刪除", self.deleteRequested.emit, True),
        ):
            b = TransparentToolButton(icon, self)
            b.setToolTip(tip)
            b.setEnabled(enabled)
            b.clicked.connect(cb)
            h.addWidget(b)

        self.clicked.connect(self.editRequested)


class OptionRow(QWidget):
    """設定卡片內的一列：左標題＋說明，右控制項。"""

    def __init__(self, title: str, hint: str, control: QWidget, parent=None):
        super().__init__(parent)
        h = QHBoxLayout(self)
        h.setContentsMargins(16, 10, 16, 10)
        v = QVBoxLayout()
        v.setSpacing(0)
        v.addWidget(BodyLabel(title, self))
        v.addWidget(muted_caption(hint, self))
        h.addLayout(v, 1)
        h.addWidget(control)


def _divider(parent) -> QFrame:
    line = QFrame(parent)
    line.setFixedHeight(1)
    line.setStyleSheet("background: rgba(128,128,128,0.18); border: none;")
    return line


class ChainEditor(QWidget):
    """右側編輯區。編輯的是 chain 的副本，按「儲存」才寫回。"""

    saveRequested = Signal(object, str)  # (TaskChain, 原名稱)
    runRequested = Signal(object)
    deleteRequested = Signal(str)
    dirtyChanged = Signal(bool)

    def __init__(self, controller: AppController, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.chain: TaskChain | None = None
        self.original_name = ""
        self._dirty = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        # 標題列
        head = QHBoxLayout()
        head.setContentsMargins(24, 16, 24, 8)
        self.nameEdit = LineEdit(self)
        self.nameEdit.setPlaceholderText("任務鏈名稱")
        self.nameEdit.setFixedWidth(280)
        self.nameEdit.textEdited.connect(lambda *_: self._mark_dirty())
        head.addWidget(self.nameEdit)
        self.dirtyLabel = muted_caption("● 未儲存", self)
        self.dirtyLabel.setTextColor("#9D5D00", "#FCE100")
        self.dirtyLabel.hide()
        head.addWidget(self.dirtyLabel)
        head.addStretch(1)
        self.saveBtn = PushButton(FIF.SAVE, "儲存", self)
        self.saveBtn.clicked.connect(self.save)
        self.runBtn = PrimaryPushButton(FIF.PLAY, "立即執行", self)
        self.runBtn.clicked.connect(self._run)
        more = TransparentToolButton(FIF.MORE, self)
        more.clicked.connect(lambda: self._more_menu(more))
        head.addWidget(self.saveBtn)
        head.addWidget(self.runBtn)
        head.addWidget(more)
        outer.addLayout(head)

        # 可捲動內容
        self.scroll = SmoothScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        body = QWidget()
        body.setStyleSheet("background: transparent;")
        self.body = QVBoxLayout(body)
        self.body.setContentsMargins(24, 0, 24, 24)
        self.body.setSpacing(6)
        self.scroll.setWidget(body)
        outer.addWidget(self.scroll, 1)

        steps_head = SectionHeader("步驟", body)
        addBtn = PushButton(FIF.ADD, "新增步驟", body)
        addBtn.clicked.connect(self._add_step)
        steps_head.addWidget(addBtn)
        self.body.addWidget(steps_head)
        self.stepsBox = QVBoxLayout()
        self.stepsBox.setSpacing(6)
        self.body.addLayout(self.stepsBox)

        # 執行前
        self.body.addWidget(SectionHeader("執行前", body))
        pre = SimpleCardWidget(body)
        pv = QVBoxLayout(pre)
        pv.setContentsMargins(0, 4, 0, 4)
        pv.setSpacing(0)
        mute_ctrl = QWidget(pre)
        mh = QHBoxLayout(mute_ctrl)
        mh.setContentsMargins(0, 0, 0, 0)
        self.muteMode = ComboBox(mute_ctrl)
        self.muteMode.addItems(["系統主音量", "逐一應用程式"])
        self.muteMode.setMinimumWidth(140)
        self.muteMode.currentIndexChanged.connect(lambda *_: self._mark_dirty())
        self.muteSwitch = SwitchButton(mute_ctrl)
        self.muteSwitch.setOnText("")
        self.muteSwitch.setOffText("")
        self.muteSwitch.checkedChanged.connect(lambda c: (self.muteMode.setEnabled(c), self._mark_dirty()))
        mh.addWidget(self.muteMode)
        mh.addWidget(self.muteSwitch)
        pv.addWidget(OptionRow("靜音", "執行期間靜音，結束（含失敗或停止）後還原原本狀態", mute_ctrl, pre))
        pv.addWidget(_divider(pre))
        self.preKill = LineEdit(pre)
        self.preKill.setPlaceholderText("例如 Discord.exe, chrome.exe")
        self.preKill.setFixedWidth(280)
        self.preKill.textEdited.connect(lambda *_: self._mark_dirty())
        pv.addWidget(OptionRow("關閉程式", "開始前先關閉這些程式，以逗號分隔", self.preKill, pre))
        self.body.addWidget(pre)

        # 執行後
        self.body.addWidget(SectionHeader("執行後", body))
        post = SimpleCardWidget(body)
        qv = QVBoxLayout(post)
        qv.setContentsMargins(0, 4, 0, 4)
        qv.setSpacing(0)
        self.postKill = LineEdit(post)
        self.postKill.setPlaceholderText("例如 Game.exe")
        self.postKill.setFixedWidth(280)
        self.postKill.textEdited.connect(lambda *_: self._mark_dirty())
        qv.addWidget(OptionRow("關閉程式", "全部步驟結束後關閉這些程式（例如遊戲本體）", self.postKill, post))
        qv.addWidget(_divider(post))
        self.powerBox = ComboBox(post)
        self._power_keys = ["none", "sleep", "hibernate", "shutdown"]
        self.powerBox.addItems(["無", "睡眠", "休眠", "關機"])
        self.powerBox.currentIndexChanged.connect(lambda *_: self._mark_dirty())
        qv.addWidget(OptionRow("電源動作", "按「停止」中斷時不會執行；關機前有 60 秒可取消", self.powerBox, post))
        self.body.addWidget(post)
        self.body.addStretch(1)

        controller.runStarted.connect(lambda *_: self._sync_run_btn())
        controller.runFinished.connect(lambda *_: self._sync_run_btn())

    # --- 載入／收集 ---

    def load(self, chain: TaskChain) -> None:
        self.chain = chain.model_copy(deep=True)
        self.original_name = chain.name
        self.nameEdit.setText(chain.name)
        mute = _hook(chain.pre_hooks, "mute")
        for w in (self.muteSwitch, self.muteMode, self.powerBox):
            w.blockSignals(True)
        self.muteSwitch.setChecked(bool(mute and mute.enabled))
        self.muteMode.setCurrentIndex(1 if mute and mute.params.get("mode") == "sessions" else 0)
        self.muteMode.setEnabled(self.muteSwitch.isChecked())
        pk = _hook(chain.pre_hooks, "kill_processes")
        self.preKill.setText(", ".join(pk.params.get("names", [])) if pk and pk.enabled else "")
        qk = _hook(chain.post_actions, "kill_processes")
        self.postKill.setText(", ".join(qk.params.get("names", [])) if qk and qk.enabled else "")
        pw = _hook(chain.post_actions, "power")
        action = pw.params.get("action", "none") if pw and pw.enabled else "none"
        self.powerBox.setCurrentIndex(self._power_keys.index(action) if action in self._power_keys else 0)
        for w in (self.muteSwitch, self.muteMode, self.powerBox):
            w.blockSignals(False)
        self._render_steps()
        self._set_dirty(False)
        self._sync_run_btn()

    def collect(self) -> TaskChain:
        assert self.chain is not None
        c = self.chain.model_copy(deep=True)
        c.name = self.nameEdit.text().strip()
        # 只改寫本頁管理的鉤子，保留手動編輯 YAML 加入的其他鉤子
        pre = [h for h in c.pre_hooks if h.type not in ("mute", "kill_processes")]
        if self.muteSwitch.isChecked():
            pre.insert(0, HookSpec(type="mute", params={"mode": "sessions" if self.muteMode.currentIndex() else "master"}))
        if _names(self.preKill.text()):
            pre.insert(0, HookSpec(type="kill_processes", params={"names": _names(self.preKill.text())}))
        post = [h for h in c.post_actions if h.type not in ("kill_processes", "power")]
        if _names(self.postKill.text()):
            post.append(HookSpec(type="kill_processes", params={"names": _names(self.postKill.text())}))
        action = self._power_keys[self.powerBox.currentIndex()]
        if action != "none":
            post.append(HookSpec(type="power", params={"action": action, "delay": 60}))
        c.pre_hooks, c.post_actions = pre, post
        return c

    # --- 步驟 ---

    def _render_steps(self) -> None:
        while self.stepsBox.count():
            item = self.stepsBox.takeAt(0)
            if item.widget():
                # deleteLater 要等回到事件迴圈才生效，先隱藏避免新舊卡片短暫重疊
                item.widget().hide()
                item.widget().deleteLater()
        assert self.chain is not None
        steps = self.chain.steps
        if not steps:
            empty = SimpleCardWidget(self)
            ev = QVBoxLayout(empty)
            ev.setContentsMargins(16, 18, 16, 18)
            ev.addWidget(muted_caption("還沒有步驟。按「新增步驟」加入要執行的腳本。", empty), 0, Qt.AlignmentFlag.AlignCenter)
            self.stepsBox.addWidget(empty)
            return
        for i, step in enumerate(steps):
            info = self.controller.registry.infos.get(step.adapter)
            task_text = ""
            if info:
                param = getattr(BUILTIN.get(info.base), "task_param", None)
                task_text = str(step.params.get(param, "") or "") if param else ""
            card = StepCard(i, step, info.name if info else f"未知（{step.adapter}）", i == len(steps) - 1, task_text, self)
            card.toggled.connect(lambda on, i=i: self._toggle_step(i, on))
            card.editRequested.connect(lambda i=i: self._edit_step(i))
            card.moveRequested.connect(lambda d, i=i: self._move_step(i, d))
            card.deleteRequested.connect(lambda i=i: self._delete_step(i))
            self.stepsBox.addWidget(card)

    def _add_step(self) -> None:
        dlg = StepDialog(self.controller.registry, None, self.window())
        if dlg.exec() and dlg.result_step:
            self.chain.steps.append(dlg.result_step)
            self._render_steps()
            self._mark_dirty()

    def _edit_step(self, i: int) -> None:
        dlg = StepDialog(self.controller.registry, self.chain.steps[i], self.window())
        if dlg.exec() and dlg.result_step:
            self.chain.steps[i] = dlg.result_step
            self._render_steps()
            self._mark_dirty()

    def _toggle_step(self, i: int, on: bool) -> None:
        self.chain.steps[i].enabled = on
        self._render_steps()
        self._mark_dirty()

    def _move_step(self, i: int, d: int) -> None:
        j = i + d
        steps = self.chain.steps
        if 0 <= j < len(steps):
            steps[i], steps[j] = steps[j], steps[i]
            self._render_steps()
            self._mark_dirty()

    def _delete_step(self, i: int) -> None:
        box = MessageBox("刪除步驟", f"確定刪除「{self.chain.steps[i].name}」？", self.window())
        box.yesButton.setText("刪除")
        box.cancelButton.setText("取消")
        if box.exec():
            del self.chain.steps[i]
            self._render_steps()
            self._mark_dirty()

    # --- 動作 ---

    def _mark_dirty(self) -> None:
        if self.chain is not None:
            self._set_dirty(True)

    def _set_dirty(self, dirty: bool) -> None:
        self._dirty = dirty
        self.dirtyLabel.setVisible(dirty)
        self.dirtyChanged.emit(dirty)

    @property
    def dirty(self) -> bool:
        return self._dirty

    def save(self) -> bool:
        if self.chain is None:
            return False
        chain = self.collect()
        if not chain.name:
            InfoBar.warning("無法儲存", "請輸入任務鏈名稱", parent=self.window(), position=InfoBarPosition.TOP)
            return False
        if chain.name != self.original_name and any(c.name == chain.name for c in self.controller.chains()):
            InfoBar.warning("無法儲存", f"已有名為「{chain.name}」的任務鏈", parent=self.window(), position=InfoBarPosition.TOP)
            return False
        self.saveRequested.emit(chain, self.original_name)
        self.chain = chain
        self.original_name = chain.name
        self._set_dirty(False)
        InfoBar.success("已儲存", chain.name, duration=1500, parent=self.window(), position=InfoBarPosition.TOP)
        return True

    def _run(self) -> None:
        if self._dirty and not self.save():
            return
        self.runRequested.emit(self.collect())

    def _sync_run_btn(self) -> None:
        self.runBtn.setEnabled(not self.controller.running)

    def _more_menu(self, anchor: QWidget) -> None:
        menu = RoundMenu(parent=self)
        menu.addAction(Action(FIF.COPY, "複製一份", triggered=lambda *_: self._duplicate()))
        menu.addAction(Action(FIF.DELETE, "刪除任務鏈", triggered=lambda *_: self._delete_chain()))
        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def _duplicate(self) -> None:
        c = self.collect()
        names = {x.name for x in self.controller.chains()}
        base = f"{c.name} 副本"
        name, n = base, 2
        while name in names:
            name, n = f"{base} {n}", n + 1
        c.name = name
        self.saveRequested.emit(c, "")

    def _delete_chain(self) -> None:
        box = MessageBox("刪除任務鏈", f"確定刪除「{self.original_name}」？此動作無法復原。", self.window())
        box.yesButton.setText("刪除")
        box.cancelButton.setText("取消")
        if box.exec():
            self.deleteRequested.emit(self.original_name)


class ChainPage(QWidget):
    runRequested = Signal(object)

    def __init__(self, controller: AppController, parent=None):
        super().__init__(parent)
        self.setObjectName("chainPage")
        self.controller = controller
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)

        # 左側清單
        side = QWidget(self)
        side.setFixedWidth(240)
        sv = QVBoxLayout(side)
        sv.setContentsMargins(16, 16, 8, 16)
        sh = QHBoxLayout()
        sh.addWidget(StrongBodyLabel("任務鏈", side))
        sh.addStretch(1)
        add = TransparentToolButton(FIF.ADD, side)
        add.setToolTip("新增任務鏈")
        add.clicked.connect(self.new_chain)
        sh.addWidget(add)
        sv.addLayout(sh)
        self.list = ListWidget(side)
        self.list.currentRowChanged.connect(self._on_select)
        sv.addWidget(self.list, 1)
        h.addWidget(side)

        sep = QFrame(self)
        sep.setFixedWidth(1)
        sep.setStyleSheet("background: rgba(128,128,128,0.18); border: none;")
        h.addWidget(sep)

        self.stack = QStackedWidget(self)
        self.empty = EmptyState(FIF.ROBOT, "還沒有任務鏈", "任務鏈是一組依序執行的腳本，例如「每日清體力」", "建立任務鏈", self)
        self.empty.actionClicked.connect(self.new_chain)
        self.editor = ChainEditor(controller, self)
        self.editor.saveRequested.connect(self._save)
        self.editor.deleteRequested.connect(self._delete)
        self.editor.runRequested.connect(self.runRequested)
        self.stack.addWidget(self.empty)
        self.stack.addWidget(self.editor)
        h.addWidget(self.stack, 1)

        self._names: list[str] = []
        self._select_after: str | None = None
        self._last_row = -1
        controller.chainsChanged.connect(self.reload)
        self.reload()

    def reload(self) -> None:
        current = self._select_after or (self._names[self.list.currentRow()] if 0 <= self.list.currentRow() < len(self._names) else None)
        self._select_after = None
        chains = self.controller.chains()
        self._names = [c.name for c in chains]
        self.list.blockSignals(True)
        self.list.clear()
        for c in chains:
            item = QListWidgetItem(c.name)
            self.list.addItem(item)
        self.list.blockSignals(False)
        if not chains:
            self.stack.setCurrentWidget(self.empty)
            self._last_row = -1
            return
        row = self._names.index(current) if current in self._names else 0
        self._last_row = -1
        self.list.setCurrentRow(row)

    def select(self, name: str) -> None:
        if name in self._names:
            self.list.setCurrentRow(self._names.index(name))

    def _on_select(self, row: int) -> None:
        if row == self._last_row:
            return
        if self.editor.dirty and self._last_row >= 0:
            box = MessageBox("尚未儲存", "目前的任務鏈有未儲存的變更，要先儲存嗎？", self.window())
            box.yesButton.setText("儲存")
            box.cancelButton.setText("放棄變更")
            target = self._names[row] if 0 <= row < len(self._names) else None
            if box.exec():
                if not self.editor.save():
                    self.list.blockSignals(True)
                    self.list.setCurrentRow(self._last_row)
                    self.list.blockSignals(False)
                    return
                # 儲存會觸發 reload 並重選剛存的鏈，再切換到使用者點選的那一個
                if target:
                    self.select(target)
                return
            self.editor._set_dirty(False)
        self._last_row = row
        if 0 <= row < len(self._names):
            chains = {c.name: c for c in self.controller.chains()}
            chain = chains.get(self._names[row])
            if chain:
                self.editor.load(chain)
                self.stack.setCurrentWidget(self.editor)

    def new_chain(self) -> None:
        names = set(self._names)
        name, n = "新任務鏈", 2
        while name in names:
            name, n = f"新任務鏈 {n}", n + 1
        self._select_after = name
        self.controller.save_chain(TaskChain(name=name))

    def _save(self, chain: TaskChain, old_name: str) -> None:
        self._select_after = chain.name
        self.controller.save_chain(chain, old_name or None)

    def _delete(self, name: str) -> None:
        self.editor._set_dirty(False)
        self.controller.delete_chain(name)
