"""介面翻譯：讓 qfluentwidgets 內建元件（開關的 On/Off、日期時間選擇器等）顯示繁體中文。"""

from __future__ import annotations

from PySide6.QtCore import QLocale
from PySide6.QtWidgets import QApplication
from qfluentwidgets import FluentTranslator

_keep: list = []  # 翻譯器需保持存活


def install_translator(app: QApplication) -> None:
    locale = QLocale(QLocale.Language.Chinese, QLocale.Country.Taiwan)
    QLocale.setDefault(locale)
    translator = FluentTranslator(locale)
    app.installTranslator(translator)
    _keep.append(translator)
