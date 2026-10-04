"""M5 介面：適配器倉庫更新對話框、本程式更新檢查（網路操作都在背景執行緒）。"""

from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    IndeterminateProgressRing,
    InfoBar,
    InfoBarPosition,
    MessageBoxBase,
    SimpleCardWidget,
    StrongBodyLabel,
    SubtitleLabel,
)

from .. import __version__
from .widgets import muted_caption

ACTION_TEXT = {"new": ("新增", "running"), "update": ("可更新", "success"), "same": ("已是最新", "skipped"),
               "skip_app_too_old": ("需更新本程式", "timeout")}


class _Bridge(QObject):
    fetched = Signal(object, object)  # (index | None, error | None)
    applied = Signal(list)
    appChecked = Signal(object)


class AdapterUpdateDialog(MessageBoxBase):
    """抓取遠端索引 → 列出差異 → 套用（只更新宣告式 YAML，舊檔備份到 adapters/.backup）。"""

    def __init__(self, controller, url: str, parent):
        super().__init__(parent)
        self.controller, self.url = controller, url
        self.index = None
        self.items = []
        self.bridge = _Bridge()
        self.bridge.fetched.connect(self._on_fetched)
        self.bridge.applied.connect(self._on_applied)

        self.viewLayout.addWidget(SubtitleLabel("適配器更新", self))
        self.viewLayout.addWidget(muted_caption(url, self))
        self.busy = QHBoxLayout()
        self.ring = IndeterminateProgressRing(self)
        self.ring.setFixedSize(22, 22)
        self.ring.setStrokeWidth(3)
        self.busyText = BodyLabel("正在讀取倉庫索引…", self)
        self.busy.addWidget(self.ring)
        self.busy.addWidget(self.busyText)
        self.busy.addStretch(1)
        self.viewLayout.addLayout(self.busy)
        self.listBox = QVBoxLayout()
        self.listBox.setSpacing(6)
        self.viewLayout.addLayout(self.listBox)
        self.yesButton.setText("套用更新")
        self.yesButton.setEnabled(False)
        self.cancelButton.setText("關閉")
        self.widget.setMinimumWidth(560)
        threading.Thread(target=self._fetch, daemon=True).start()

    def _fetch(self) -> None:
        from ..core.adapter_repo import fetch_index

        try:
            self.bridge.fetched.emit(fetch_index(self.url), None)
        except Exception as e:
            self.bridge.fetched.emit(None, str(e))

    def _on_fetched(self, index, error) -> None:
        self.ring.hide()
        if error:
            self.busyText.setText(f"讀取失敗：{error}")
            return
        from ..core.adapter_repo import plan

        self.index = index
        self.items = plan(index, self.controller.registry.infos, __version__)
        pending = [i for i in self.items if i.action in ("new", "update")]
        self.busyText.setText(f"倉庫有 {len(self.items)} 個適配器，{len(pending)} 個可新增或更新。" if self.items else "倉庫是空的。")
        from .widgets import StatusBadge

        for it in self.items:
            card = SimpleCardWidget(self)
            h = QHBoxLayout(card)
            h.setContentsMargins(14, 8, 14, 8)
            v = QVBoxLayout()
            v.setSpacing(0)
            v.addWidget(StrongBodyLabel(it.name or it.id, card))
            ver = f"本機 v{it.local_version}" if it.local_version is not None else "本機沒有"
            v.addWidget(muted_caption(f"{ver} → 倉庫 v{it.remote_version}{'　' + it.note if it.note else ''}", card))
            h.addLayout(v, 1)
            text, status = ACTION_TEXT.get(it.action, (it.action, "idle"))
            badge = StatusBadge(status, card)
            badge.setText(text)
            h.addWidget(badge)
            self.listBox.addWidget(card)
        self.yesButton.setEnabled(bool(pending))

    def validate(self) -> bool:
        if not self.index:
            return True
        self.yesButton.setEnabled(False)
        self.ring.show()
        self.busyText.setText("正在下載並驗證…")

        def work():
            from ..core.adapter_repo import apply

            try:
                msgs = apply(self.url, self.index, self.items, self.controller.paths.adapters)
            except Exception as e:
                msgs = [f"更新失敗：{e}"]
            self.bridge.applied.emit(msgs)

        threading.Thread(target=work, daemon=True).start()
        return False  # 完成後由 _on_applied 關閉

    def _on_applied(self, msgs: list) -> None:
        from ..core.registry import AdapterRegistry

        reg = AdapterRegistry()
        reg.load_dir(self.controller.paths.adapters)
        self.controller.registry.infos = reg.infos
        self.controller.registry.errors = reg.errors
        self.controller.chainsChanged.emit()  # 讓步驟卡片等重新讀取適配器名稱
        InfoBar.success("適配器已更新", "；".join(msgs[:4]), duration=6000, parent=self.parent(), position=InfoBarPosition.TOP)
        self.accept()


def check_app_update(repo: str, parent: QWidget, silent: bool = False) -> None:
    """背景檢查 GitHub Release；有新版時在視窗上方提示。silent=True 時沒有新版或出錯都不提示。"""
    bridge = _Bridge(parent)

    def done(info) -> None:
        if info.available:
            bar = InfoBar.success(
                f"有新版本 {info.latest}", f"目前 {info.current}。{(info.notes or '')[:80]}",
                duration=-1, parent=parent, position=InfoBarPosition.TOP,
            )
            if info.url:
                from qfluentwidgets import HyperlinkButton

                bar.addWidget(HyperlinkButton(info.url, "前往下載", bar))
        elif not silent:
            if info.error:
                InfoBar.warning("無法檢查更新", info.error, duration=6000, parent=parent, position=InfoBarPosition.TOP)
            else:
                InfoBar.info("已是最新版本", f"目前 {info.current}", duration=3000, parent=parent, position=InfoBarPosition.TOP)
        bridge.deleteLater()

    bridge.appChecked.connect(done)

    def work():
        from ..core.app_update import check_github

        try:
            info = check_github(repo, __version__)
        except Exception as e:
            from ..core.app_update import UpdateInfo

            info = UpdateInfo(available=False, current=__version__, latest=None, url=None, notes="", error=str(e))
        bridge.appChecked.emit(info)

    threading.Thread(target=work, daemon=True).start()
