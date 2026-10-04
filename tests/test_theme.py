import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_overrides_fully_substituted(app):
    from gachahub.gui import theme

    for dark in (False, True):
        for name in theme.OVERRIDES:
            qss = theme._render_override(name, dark)
            assert "@" not in qss, (name, dark)


def test_square_painter_installed(app):
    from gachahub.gui import theme

    theme.install()
    from qfluentwidgets.components.widgets import card_widget

    assert card_widget.QPainter is theme._SquarePainter


def test_toggle_theme_button(app, tmp_path, monkeypatch):
    monkeypatch.setenv("GACHAHUB_ROOT", str(tmp_path))
    from qfluentwidgets import Theme, isDarkTheme, qconfig, setTheme

    monkeypatch.setattr(qconfig, "file", tmp_path / "qconfig.json")  # 切換主題會存檔，不可寫到專案資料夾

    from gachahub.core.config import Paths
    from gachahub.gui import main_window as mw
    from gachahub.gui.controller import AppController
    from gachahub.gui.settings_page import cfg

    monkeypatch.setattr(cfg.hotkeyEnabled, "value", False)
    monkeypatch.setattr(cfg.wizardDone, "value", True)
    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda *exc: errors.append(exc))
    paths = Paths(tmp_path)
    paths.ensure()
    setTheme(Theme.LIGHT)
    w = mw.MainWindow(AppController(paths))
    try:
        w.themeButton.click()
        app.processEvents()
        assert isDarkTheme()
        assert w.themeButton.toolTip() == "切換為淺色"
        w.themeButton.click()
        app.processEvents()
        assert not isDarkTheme()
        assert not errors, errors
    finally:
        setTheme(Theme.LIGHT)
        w.tray.hide()
        w._shutdown()
