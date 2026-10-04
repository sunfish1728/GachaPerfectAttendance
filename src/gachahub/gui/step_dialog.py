"""步驟編輯對話框：依腳本類型顯示對應表單，其餘參數用 YAML。

- 通用程式：執行檔＋命令列參數
- 有安裝資料夾的腳本（OK 系列、一條龍等）：資料夾＋自動偵測＋任務下拉選單＋勾選選項
"""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

import yaml
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QFileDialog, QFormLayout, QHBoxLayout, QWidget
from qfluentwidgets import (
    Action,
    CheckBox,
    ComboBox,
    FluentIcon as FIF,
    LineEdit,
    MessageBoxBase,
    PlainTextEdit,
    PushButton,
    RoundMenu,
    SpinBox,
    SubtitleLabel,
    ToolButton,
)

from ..core.adapter import Adapter
from ..core.models import FailPolicy, TaskStep
from ..core.registry import AdapterRegistry
from .widgets import mono_font, muted_caption

POLICIES = [(FailPolicy.SKIP, "跳過，繼續下一步"), (FailPolicy.RETRY, "重試"), (FailPolicy.ABORT, "中止整條任務鏈")]


def _args_to_text(args: list[str]) -> str:
    return " ".join(f'"{a}"' if (" " in a or not a) else a for a in args)


def _split_args(text: str) -> list[str]:
    # posix=False 才不會吃掉 Windows 路徑中的反斜線；再自行去掉外層引號
    out = []
    for tok in shlex.split(text, posix=False):
        if len(tok) >= 2 and tok[0] == tok[-1] and tok[0] in "\"'":
            tok = tok[1:-1]
        out.append(tok)
    return out


def _form_keys(adapter: Adapter) -> set[str]:
    """由表單管理的參數名稱（不出現在 YAML 進階參數中）。"""
    if not adapter.uses_install_dir:
        return {"command", "args"}
    keys = {"install_dir", *(k for k, _, _ in adapter.bool_options)}
    if adapter.task_param:
        keys.add(adapter.task_param)
    return keys


class StepDialog(MessageBoxBase):
    def __init__(self, registry: AdapterRegistry, step: TaskStep | None, parent: QWidget):
        super().__init__(parent)
        self.registry = registry
        step = step or TaskStep(name="", adapter="generic")
        self.result_step: TaskStep | None = None
        self._enabled = step.enabled
        self._orig_params = dict(step.params)
        self._loaded_adapter: str | None = None
        self._task_values: list[str] = []

        self.viewLayout.addWidget(SubtitleLabel("編輯步驟" if step.name else "新增步驟", self))
        self.form = form = QFormLayout()
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        self.nameEdit = LineEdit(self)
        self.nameEdit.setPlaceholderText("例如：異環日常")
        self.nameEdit.setText(step.name)
        form.addRow("名稱", self.nameEdit)

        self.adapterBox = ComboBox(self)
        self._adapter_ids = list(registry.infos)
        for aid in self._adapter_ids:
            self.adapterBox.addItem(registry.infos[aid].name)
        self._missing_adapter = step.adapter not in self._adapter_ids
        if not self._missing_adapter:
            self.adapterBox.setCurrentIndex(self._adapter_ids.index(step.adapter))
        form.addRow("腳本類型", self.adapterBox)

        # 通用程式
        self.cmdRow = QWidget(self)
        cr = QHBoxLayout(self.cmdRow)
        cr.setContentsMargins(0, 0, 0, 0)
        self.cmdEdit = LineEdit(self.cmdRow)
        browse = ToolButton(FIF.FOLDER, self.cmdRow)
        browse.setToolTip("選擇執行檔")
        browse.clicked.connect(self._browse_exe)
        cr.addWidget(self.cmdEdit, 1)
        cr.addWidget(browse)
        form.addRow("執行檔", self.cmdRow)
        self.argsEdit = LineEdit(self)
        self.argsEdit.setPlaceholderText("以空白分隔，含空白的參數請加引號")
        form.addRow("命令列參數", self.argsEdit)

        # 有安裝資料夾的腳本
        self.dirRow = QWidget(self)
        dr = QHBoxLayout(self.dirRow)
        dr.setContentsMargins(0, 0, 0, 0)
        self.dirEdit = LineEdit(self.dirRow)
        self.dirEdit.setPlaceholderText("腳本的安裝資料夾")
        self.dirEdit.editingFinished.connect(lambda: self._reload_tasks())
        dbrowse = ToolButton(FIF.FOLDER, self.dirRow)
        dbrowse.setToolTip("選擇資料夾")
        dbrowse.clicked.connect(self._browse_dir)
        self.detectBtn = PushButton(FIF.SEARCH, "自動偵測", self.dirRow)
        self.detectBtn.clicked.connect(self._detect)
        dr.addWidget(self.dirEdit, 1)
        dr.addWidget(dbrowse)
        dr.addWidget(self.detectBtn)
        form.addRow("安裝資料夾", self.dirRow)

        self.taskRow = QWidget(self)
        tr = QHBoxLayout(self.taskRow)
        tr.setContentsMargins(0, 0, 0, 0)
        self.taskBox = ComboBox(self.taskRow)
        self.taskBox.setPlaceholderText("先選擇安裝資料夾")
        refresh = ToolButton(FIF.SYNC, self.taskRow)
        refresh.setToolTip("重新讀取腳本的任務清單")
        refresh.clicked.connect(lambda: self._reload_tasks())
        tr.addWidget(self.taskBox, 1)
        tr.addWidget(refresh)
        form.addRow("任務", self.taskRow)
        self.taskHint = muted_caption("", self)
        self.taskHint.setWordWrap(True)
        form.addRow("", self.taskHint)

        self.optRow = QWidget(self)
        self.optLayout = QHBoxLayout(self.optRow)
        self.optLayout.setContentsMargins(0, 0, 0, 0)
        self.optLayout.setSpacing(20)
        self._opt_boxes: dict[str, CheckBox] = {}
        form.addRow("選項", self.optRow)

        self.timeoutBox = SpinBox(self)
        self.timeoutBox.setRange(1, 24 * 60)
        self.timeoutBox.setSuffix(" 分鐘")
        self.timeoutBox.setValue(max(1, round(step.timeout / 60)))
        form.addRow("逾時上限", self.timeoutBox)

        pol = QWidget(self)
        pr = QHBoxLayout(pol)
        pr.setContentsMargins(0, 0, 0, 0)
        self.policyBox = ComboBox(pol)
        for _, text in POLICIES:
            self.policyBox.addItem(text)
        self.policyBox.setCurrentIndex([p for p, _ in POLICIES].index(step.on_fail))
        self.retryBox = SpinBox(pol)
        self.retryBox.setRange(1, 10)
        self.retryBox.setSuffix(" 次")
        self.retryBox.setValue(max(1, step.retries))
        self.policyBox.currentIndexChanged.connect(self._sync_retry)
        pr.addWidget(self.policyBox, 1)
        pr.addWidget(self.retryBox)
        form.addRow("失敗時", pol)

        self.viewLayout.addLayout(form)

        self.viewLayout.addWidget(muted_caption("進階參數（YAML）", self))
        self.fieldHint = muted_caption("", self)
        self.fieldHint.setWordWrap(True)
        self.viewLayout.addWidget(self.fieldHint)
        self.yamlEdit = PlainTextEdit(self)
        self.yamlEdit.setFont(mono_font())
        self.yamlEdit.setFixedHeight(96)
        self.viewLayout.addWidget(self.yamlEdit)

        self.errorLabel = muted_caption("", self)
        self.errorLabel.setTextColor("#C42B1C", "#FF99A4")
        self.errorLabel.setWordWrap(True)
        self.errorLabel.hide()
        self.viewLayout.addWidget(self.errorLabel)

        self.yesButton.setText("確定")
        self.cancelButton.setText("取消")
        self.widget.setMinimumWidth(580)
        self.adapterBox.currentIndexChanged.connect(self._on_adapter_changed)
        self._on_adapter_changed()
        self._sync_retry()
        if self._missing_adapter:
            # 不默默改用別的類型：明確提示，避免使用者按確定後覆蓋原設定
            self._fail(f"找不到原本的腳本類型「{step.adapter}」（adapters 資料夾中的定義可能被移除），請重新選擇")

    # --- 依適配器切換表單 ---

    def _adapter_id(self) -> str:
        return self._adapter_ids[self.adapterBox.currentIndex()]

    def _adapter(self) -> Adapter:
        return self.registry.create(self._adapter_id())

    def _on_adapter_changed(self, *_) -> None:
        # 切換前把表單上的值收回，讓使用者切來切去不會遺失輸入
        if self._loaded_adapter is not None:
            try:
                self._orig_params.update(self._collect_params(strict=False))
            except Exception:
                pass
        adapter = self._adapter()
        info = self.registry.infos[self._adapter_id()]
        p = self._orig_params
        self._loaded_adapter = self._adapter_id()

        generic = not adapter.uses_install_dir
        for w in (self.cmdRow, self.argsEdit):
            self.form.setRowVisible(w, generic)
        for w in (self.dirRow,):
            self.form.setRowVisible(w, not generic)
        self.form.setRowVisible(self.taskRow, bool(adapter.task_param))
        self.form.setRowVisible(self.taskHint, bool(adapter.task_param))
        self.form.setRowVisible(self.optRow, bool(adapter.bool_options))
        label = self.form.labelForField(self.taskRow)
        if label is not None:
            label.setText(adapter.task_label)

        if generic:
            self.cmdEdit.setText(str(p.get("command", "")))
            self.cmdEdit.setCursorPosition(0)
            default_cmd = info.defaults.get("command")
            self.cmdEdit.setPlaceholderText(f"預設：{default_cmd}" if default_cmd else "例如 D:/Tools/script.exe")
            self.argsEdit.setText(_args_to_text([str(a) for a in p["args"]]) if "args" in p else "")
        else:
            self.dirEdit.setText(str(p.get("install_dir", "")))
            self.dirEdit.setCursorPosition(0)
            self.taskBox.clear()
            self._task_values = []
            self._reload_tasks(keep_selection=False)

        # 勾選選項
        while self.optLayout.count():
            item = self.optLayout.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        self._opt_boxes.clear()
        for key, text, default in adapter.bool_options:
            box = CheckBox(text, self.optRow)
            box.setChecked(bool(p.get(key, info.defaults.get(key, default))))
            self.optLayout.addWidget(box)
            self._opt_boxes[key] = box
        self.optLayout.addStretch(1)

        keys = _form_keys(adapter)
        extra = {k: v for k, v in p.items() if k not in keys and k not in ("command", "args", "install_dir")}
        self.yamlEdit.setPlainText(yaml.safe_dump(extra, allow_unicode=True, sort_keys=False).strip() if extra else "")
        self.yamlEdit.setPlaceholderText(
            "completion: process_gone\nprocess_names: [Game.exe]\nkill_on_finish: [Game.exe]" if generic
            else "startup_grace: 90"
        )
        hints = [f"{f.get('key')}：{f.get('label', '')}" for f in info.fields if f.get("key") not in keys]
        self.fieldHint.setText("可用參數 — " + "；".join(hints) if hints else "")
        self.fieldHint.setVisible(bool(hints))
        self.errorLabel.hide()

    # --- 安裝資料夾與任務 ---

    def _reload_tasks(self, keep_selection: bool = True) -> None:
        adapter = self._adapter()
        if not adapter.task_param:
            return
        if keep_selection and self._task_values:
            self._orig_params[adapter.task_param] = self._task_value()
        current = str(self._orig_params.get(adapter.task_param, "") or "")
        self.taskBox.clear()
        self._task_values = []
        install = self.dirEdit.text().strip()
        if not install:
            self.taskHint.setText("選擇安裝資料夾後會自動讀取腳本中的任務")
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            options = adapter.list_tasks({**self._orig_params, "install_dir": install})
        finally:
            QApplication.restoreOverrideCursor()
        for opt in options:
            self.taskBox.addItem(opt.label)
            self._task_values.append(opt.value)
        if current and current not in self._task_values:
            # 已儲存的任務在目前腳本中找不到：保留並標示，讓使用者知道腳本可能改版
            self.taskBox.addItem(f"{current}（目前腳本中找不到）")
            self._task_values.append(current)
        if current in self._task_values:
            self.taskBox.setCurrentIndex(self._task_values.index(current))
        elif self._task_values:
            self.taskBox.setCurrentIndex(0)
        if options:
            hint = f"已從腳本讀取 {len(options)} 個選項；腳本更新後會依名稱自動對應"
        else:
            hint = "無法從這個資料夾讀取清單，請確認是否為正確的安裝資料夾"
        self.taskHint.setText(hint + self._version_text(adapter, {**self._orig_params, "install_dir": install}))

    def _version_text(self, adapter, params) -> str:
        """偵測到的腳本版本與相容性（M5）。"""
        try:
            from ..core import compat

            info = self.registry.infos[self._adapter_id()]
            result = compat.check(info, adapter, params)
            version = result.version or adapter.script_version(params)
        except Exception:
            return ""
        if not version:
            return ""
        if not result.ok:
            return f"\n⚠ 偵測到版本 {version}：{result.message}"
        return f"\n偵測到版本 {version}" +("（在已驗證範圍內）" if info.compat else "")

    def _task_value(self) -> str:
        i = self.taskBox.currentIndex()
        return self._task_values[i] if 0 <= i < len(self._task_values) else ""

    def _browse_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "選擇安裝資料夾", self.dirEdit.text())
        if path:
            self._set_dir(path)

    def _set_dir(self, path: str) -> None:
        self.dirEdit.setText(str(Path(path)))
        self.dirEdit.setCursorPosition(0)
        self._reload_tasks()
        if not self.nameEdit.text().strip():
            self.nameEdit.setText(self.registry.infos[self._adapter_id()].name)

    def _detect(self) -> None:
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            found = self._adapter().detect()
        except Exception:
            found = []
        finally:
            QApplication.restoreOverrideCursor()
        if not found:
            self._fail("找不到已安裝的位置，請手動選擇資料夾")
            return
        self.errorLabel.hide()
        if len(found) == 1:
            self._set_dir(str(found[0]))
            return
        menu = RoundMenu(parent=self)
        for path in found:
            menu.addAction(Action(FIF.FOLDER, str(path), triggered=lambda _=False, p=path: self._set_dir(str(p))))
        menu.exec(self.detectBtn.mapToGlobal(self.detectBtn.rect().bottomLeft()))

    def _browse_exe(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "選擇執行檔", "", "程式 (*.exe *.bat *.cmd *.ps1);;所有檔案 (*)")
        if path:
            self.cmdEdit.setText(path)
            if not self.nameEdit.text().strip():
                self.nameEdit.setText(Path(path).stem)

    def _sync_retry(self, *_) -> None:
        self.retryBox.setEnabled(POLICIES[self.policyBox.currentIndex()][0] == FailPolicy.RETRY)

    # --- 收集與驗證 ---

    def _collect_params(self, strict: bool = True) -> dict[str, Any]:
        adapter = self.registry.create(self._loaded_adapter or self._adapter_id())
        extra: Any = yaml.safe_load(self.yamlEdit.toPlainText()) or {}
        if not isinstance(extra, dict):
            raise ValueError("進階參數必須是「鍵: 值」的形式")
        params: dict[str, Any] = dict(extra)
        if not adapter.uses_install_dir:
            if self.cmdEdit.text().strip():
                params["command"] = self.cmdEdit.text().strip()
            if self.argsEdit.text().strip():
                params["args"] = _split_args(self.argsEdit.text())
        else:
            if self.dirEdit.text().strip():
                params["install_dir"] = self.dirEdit.text().strip()
            if adapter.task_param:
                value = self._task_value()
                if value or not strict:
                    params[adapter.task_param] = value
            for key, box in self._opt_boxes.items():
                params[key] = box.isChecked()
        return params

    def _fail(self, msg: str) -> bool:
        self.errorLabel.setText(msg)
        self.errorLabel.show()
        return False

    def validate(self) -> bool:
        name = self.nameEdit.text().strip()
        if not name:
            return self._fail("請輸入步驟名稱")
        try:
            params = self._collect_params()
        except yaml.YAMLError as e:
            return self._fail(f"YAML 格式錯誤：{getattr(e, 'problem', e)}")
        except ValueError as e:
            return self._fail(str(e))
        adapter_id = self._adapter_id()
        try:
            self.registry.create(adapter_id).validate(params)
        except Exception as e:
            return self._fail(str(e))
        self.result_step = TaskStep(
            name=name,
            adapter=adapter_id,
            params=params,
            timeout=self.timeoutBox.value() * 60.0,
            on_fail=POLICIES[self.policyBox.currentIndex()][0],
            retries=self.retryBox.value(),
            enabled=self._enabled,
        )
        return True
