"""托盤選單回歸測試：Action 觸發時會附帶 checked 參數，任何項目都不可因此丟出例外（曾導致「離開」無效）。"""

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_every_tray_action_triggers_cleanly(app, tmp_path, monkeypatch):
    monkeypatch.setenv("GACHAHUB_ROOT", str(tmp_path))
    from gachahub.core.config import Paths
    from gachahub.gui import main_window as mw
    from gachahub.gui.controller import AppController
    from gachahub.gui.settings_page import cfg

    monkeypatch.setattr(cfg.hotkeyEnabled, "value", False)  # 測試不註冊全域熱鍵
    monkeypatch.setattr(cfg.wizardDone, "value", True)
    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda *exc: errors.append(exc))
    quits = []
    monkeypatch.setattr(mw.QApplication, "quit", staticmethod(lambda: quits.append(True)))

    paths = Paths(tmp_path)
    paths.ensure()
    w = mw.MainWindow(AppController(paths))
    try:
        w.tray._rebuild()
        actions = {a.text(): a for a in w.tray.menu.actions() if a.text()}
        assert "離開" in actions and "顯示主視窗" in actions
        for text, action in actions.items():
            if action.isEnabled():
                action.trigger()
                app.processEvents()
        assert not errors, errors
        assert quits, "按「離開」後應該呼叫 QApplication.quit()"
    finally:
        w.tray.hide()
        w._shutdown()
