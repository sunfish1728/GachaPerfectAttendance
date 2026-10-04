"""通知頁：推播事件、管道清單（Telegram、Discord、Server醬、Bark、Email、Webhook）。

管道表單依 core.notify.CHANNEL_TYPES 自動產生；密鑰欄位用密碼框。設定存於 data/notify.yaml。
"""

from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QFormLayout, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    Action,
    BodyLabel,
    CardWidget,
    ComboBox,
    FluentIcon as FIF,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    MessageBox,
    MessageBoxBase,
    PasswordLineEdit,
    PrimaryPushButton,
    PushButton,
    RoundMenu,
    SimpleCardWidget,
    SmoothScrollArea,
    StrongBodyLabel,
    SubtitleLabel,
    SwitchButton,
    TitleLabel,
    TransparentToolButton,
)

from .controller import AppController
from .home_page import IconTile
from .widgets import EmptyState, SectionHeader, muted_caption

TYPE_ICON = {"telegram": FIF.SEND, "discord": FIF.CHAT, "serverchan": FIF.MESSAGE, "bark": FIF.RINGER,
             "email": FIF.MAIL, "webhook": FIF.LINK}
EVENTS = [("on_success", "全部成功時"), ("on_failure", "有步驟失敗時"), ("on_cancel", "被停止時"),
          ("on_schedule_skip", "排程因錯過太久而略過時")]


class _Bridge(QObject):
    done = Signal(str, bool, str)


class ChannelDialog(MessageBoxBase):
    def __init__(self, channel, parent):
        super().__init__(parent)
        from ..core.notify import CHANNEL_TYPES, Channel

        self.types = list(CHANNEL_TYPES.values())
        self.channel = channel or Channel(type=self.types[0].type)
        self.result_channel = None
        self.viewLayout.addWidget(SubtitleLabel("編輯通知管道" if channel else "新增通知管道", self))
        top = QFormLayout()
        top.setHorizontalSpacing(16)
        top.setVerticalSpacing(10)
        top.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.typeBox = ComboBox(self)
        for t in self.types:
            self.typeBox.addItem(t.label)
        idx = next((i for i, t in enumerate(self.types) if t.type == self.channel.type), 0)
        self.typeBox.setCurrentIndex(idx)
        self.typeBox.setEnabled(channel is None)  # 既有管道不換類型，避免欄位錯亂
        top.addRow("類型", self.typeBox)
        self.nameEdit = LineEdit(self)
        self.nameEdit.setPlaceholderText("自訂名稱（選填），例如「手機」")
        self.nameEdit.setText(self.channel.name)
        top.addRow("名稱", self.nameEdit)
        self.viewLayout.addLayout(top)

        self.fieldsHost = QWidget(self)
        self.fieldsForm = QFormLayout(self.fieldsHost)
        self.fieldsForm.setContentsMargins(0, 0, 0, 0)
        self.fieldsForm.setHorizontalSpacing(16)
        self.fieldsForm.setVerticalSpacing(10)
        self.fieldsForm.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.viewLayout.addWidget(self.fieldsHost)
        self.edits: dict[str, LineEdit] = {}

        self.secretHint = muted_caption("密鑰以明文存於本機 data/notify.yaml，請勿分享這個檔案。", self)
        self.viewLayout.addWidget(self.secretHint)
        self.errorLabel = muted_caption("", self)
        self.errorLabel.setTextColor("#C42B1C", "#FF99A4")
        self.errorLabel.hide()
        self.viewLayout.addWidget(self.errorLabel)
        self.yesButton.setText("確定")
        self.cancelButton.setText("取消")
        self.widget.setMinimumWidth(540)
        self.typeBox.currentIndexChanged.connect(self._build_fields)
        self._build_fields()

    def _spec(self):
        return self.types[self.typeBox.currentIndex()]

    def _build_fields(self, *_) -> None:
        while self.fieldsForm.rowCount():
            self.fieldsForm.removeRow(0)
        self.edits.clear()
        spec = self._spec()
        params = self.channel.params if self.channel.type == spec.type else {}
        for f in spec.fields:
            edit = PasswordLineEdit(self.fieldsHost) if f.secret else LineEdit(self.fieldsHost)
            edit.setPlaceholderText(f.placeholder or (f"預設：{f.default}" if f.default else ""))
            edit.setText(params.get(f.key, ""))
            edit.setClearButtonEnabled(not f.secret)
            label = f.label + ("" if f.required else "（選填）")
            if f.help:
                col = QWidget(self.fieldsHost)
                cv = QVBoxLayout(col)
                cv.setContentsMargins(0, 0, 0, 0)
                cv.setSpacing(2)
                cv.addWidget(edit)
                h = muted_caption(f.help, col)
                h.setWordWrap(True)
                cv.addWidget(h)
                self.fieldsForm.addRow(label, col)
            else:
                self.fieldsForm.addRow(label, edit)
            self.edits[f.key] = edit
        self.secretHint.setVisible(any(f.secret for f in spec.fields))

    def validate(self) -> bool:
        spec = self._spec()
        params = {k: e.text().strip() for k, e in self.edits.items() if e.text().strip()}
        missing = [f.label for f in spec.fields if f.required and not params.get(f.key)]
        if missing:
            self.errorLabel.setText("請填寫：" + "、".join(missing))
            self.errorLabel.show()
            return False
        self.result_channel = self.channel.model_copy(update={
            "type": spec.type, "name": self.nameEdit.text().strip(), "params": params,
        })
        return True


class ChannelCard(CardWidget):
    def __init__(self, channel, page: "NotifyPage"):
        super().__init__(page)
        from ..core.notify import CHANNEL_TYPES

        self.channel, self.page = channel, page
        spec = CHANNEL_TYPES.get(channel.type)
        self.setFixedHeight(72)
        h = QHBoxLayout(self)
        h.setContentsMargins(16, 10, 12, 10)
        h.setSpacing(14)
        h.addWidget(IconTile(TYPE_ICON.get(channel.type, FIF.RINGER), 40, self))
        v = QVBoxLayout()
        v.setSpacing(0)
        v.addWidget(StrongBodyLabel(channel.name or (spec.label if spec else channel.type), self))
        v.addWidget(muted_caption(spec.label if spec else f"未知類型：{channel.type}", self))
        h.addLayout(v, 1)
        test = PushButton(FIF.SEND, "測試", self)
        test.clicked.connect(lambda: page.test(channel))
        h.addWidget(test)
        sw = SwitchButton(self)
        sw.setOnText("")
        sw.setOffText("")
        sw.setChecked(channel.enabled)
        sw.checkedChanged.connect(lambda on: page.update_channel(channel.model_copy(update={"enabled": on})))
        h.addWidget(sw)
        more = TransparentToolButton(FIF.MORE, self)
        more.clicked.connect(lambda: self._menu(more))
        h.addWidget(more)
        self.clicked.connect(lambda: page.edit(channel))

    def _menu(self, anchor) -> None:
        m = RoundMenu(parent=self)
        m.addAction(Action(FIF.EDIT, "編輯", triggered=lambda: self.page.edit(self.channel)))
        m.addAction(Action(FIF.DELETE, "刪除", triggered=lambda: self.page.delete(self.channel)))
        m.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))


class NotifyPage(SmoothScrollArea):
    def __init__(self, controller: AppController, parent=None):
        super().__init__(parent)
        self.setObjectName("notifyPage")
        self.controller = controller
        self.bridge = _Bridge()
        self.bridge.done.connect(self._on_test_done)
        self.setWidgetResizable(True)
        self.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        body = QWidget()
        body.setStyleSheet("background: transparent;")
        v = QVBoxLayout(body)
        v.setContentsMargins(32, 28, 32, 32)
        v.setSpacing(8)
        self.setWidget(body)

        head = QHBoxLayout()
        head.addWidget(TitleLabel("通知", body))
        head.addStretch(1)
        add = PrimaryPushButton(FIF.ADD, "新增管道", body)
        add.clicked.connect(lambda: self.edit(None))
        head.addWidget(add)
        v.addLayout(head)
        v.addWidget(muted_caption("任務鏈結束時推播到手機或其他裝置；Windows 托盤通知一律會顯示。", body))

        v.addWidget(SectionHeader("什麼時候通知", body))
        ev = SimpleCardWidget(body)
        el = QVBoxLayout(ev)
        el.setContentsMargins(16, 6, 16, 6)
        el.setSpacing(0)
        self.eventSwitches: dict[str, SwitchButton] = {}
        for key, text in EVENTS:
            row = QHBoxLayout()
            row.setContentsMargins(0, 6, 0, 6)
            row.addWidget(BodyLabel(text, ev), 1)
            sw = SwitchButton(ev)
            sw.setOnText("")
            sw.setOffText("")
            sw.checkedChanged.connect(lambda on, k=key: self._set_event(k, on))
            row.addWidget(sw)
            self.eventSwitches[key] = sw
            el.addLayout(row)
        v.addWidget(ev)

        v.addWidget(SectionHeader("通知管道", body))
        self.listBox = QVBoxLayout()
        self.listBox.setSpacing(6)
        v.addLayout(self.listBox)
        v.addStretch(1)
        self.reload()

    def _config(self):
        return self.controller.notify_config()

    def reload(self) -> None:
        cfg = self._config()
        for key, sw in self.eventSwitches.items():
            sw.blockSignals(True)
            sw.setChecked(bool(getattr(cfg, key)))
            sw.blockSignals(False)
        while self.listBox.count():
            item = self.listBox.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        if not cfg.channels:
            empty = EmptyState(FIF.RINGER, "還沒有通知管道", "加入 Telegram、Discord、Bark 等，在外面也能知道日常有沒有跑完", "新增管道", self)
            empty.actionClicked.connect(lambda: self.edit(None))
            self.listBox.addWidget(empty)
        for ch in cfg.channels:
            self.listBox.addWidget(ChannelCard(ch, self))

    def _save(self, cfg) -> None:
        self.controller.save_notify_config(cfg)
        self.reload()

    def _set_event(self, key: str, on: bool) -> None:
        cfg = self._config()
        setattr(cfg, key, on)
        self.controller.save_notify_config(cfg)

    def edit(self, channel) -> None:
        dlg = ChannelDialog(channel, self.window())
        if dlg.exec() and dlg.result_channel:
            self.update_channel(dlg.result_channel)

    def update_channel(self, channel) -> None:
        cfg = self._config()
        for i, c in enumerate(cfg.channels):
            if c.id == channel.id:
                cfg.channels[i] = channel
                break
        else:
            cfg.channels.append(channel)
        self._save(cfg)

    def delete(self, channel) -> None:
        box = MessageBox("刪除通知管道", f"確定刪除「{channel.name or channel.type}」？", self.window())
        box.yesButton.setText("刪除")
        box.cancelButton.setText("取消")
        if box.exec():
            cfg = self._config()
            cfg.channels = [c for c in cfg.channels if c.id != channel.id]
            self._save(cfg)

    def test(self, channel) -> None:
        from ..core.notify import send

        InfoBar.info("正在發送測試通知…", channel.name or channel.type, duration=2000,
                     parent=self.window(), position=InfoBarPosition.TOP)

        def work():
            ok, msg = send(channel, "🔔 二遊全勤君 測試通知", "看到這則訊息代表通知設定正確。")
            self.bridge.done.emit(channel.name or channel.type, ok, msg)

        threading.Thread(target=work, daemon=True).start()

    def _on_test_done(self, name: str, ok: bool, msg: str) -> None:
        if ok:
            InfoBar.success("測試通知已送出", f"{name}：{msg}", duration=3000, parent=self.window(), position=InfoBarPosition.TOP)
        else:
            InfoBar.error("測試通知失敗", f"{name}：{msg}", duration=8000, parent=self.window(), position=InfoBarPosition.TOP)
