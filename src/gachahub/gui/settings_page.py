"""設定頁與介面設定（存於 data/settings.json）。"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QVBoxLayout, QWidget
from qfluentwidgets import (
    BoolValidator,
    ConfigItem,
    CustomColorSettingCard,
    ExpandLayout,
    FluentIcon as FIF,
    OptionsSettingCard,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    PrimaryPushSettingCard,
    PushButton,
    RangeSettingCard,
    SettingCard,
    QConfig,
    RangeConfigItem,
    RangeValidator,
    SettingCardGroup,
    SmoothScrollArea,
    SwitchSettingCard,
    TitleLabel,
    qconfig,
    setTheme,
    setThemeColor,
)

from .. import __version__

DEFAULT_ACCENT = "#7A5AF8"


class AppConfig(QConfig):
    closeToTray = ConfigItem("Window", "CloseToTray", True, BoolValidator())
    trayHintShown = ConfigItem("Window", "TrayHintShown", False, BoolValidator())
    autoElevate = ConfigItem("General", "AutoElevate", True, BoolValidator())
    # 排程與保護
    countdownSeconds = RangeConfigItem("Schedule", "CountdownSeconds", 60, RangeValidator(0, 600))
    presenceEnabled = ConfigItem("Presence", "Enabled", True, BoolValidator())
    presenceIdleMinutes = RangeConfigItem("Presence", "IdleMinutes", 3, RangeValidator(1, 60))
    presenceFullscreen = ConfigItem("Presence", "Fullscreen", True, BoolValidator())
    presenceDeferMinutes = RangeConfigItem("Presence", "DeferMinutes", 15, RangeValidator(1, 180))
    presenceMaxDefers = RangeConfigItem("Presence", "MaxDefers", 4, RangeValidator(0, 20))
    hotkeyEnabled = ConfigItem("Hotkey", "Enabled", True, BoolValidator())
    hotkey = ConfigItem("Hotkey", "Stop", "ctrl+shift+f12")
    # 首次使用與更新
    wizardDone = ConfigItem("General", "WizardDone", False, BoolValidator())
    adapterRepoUrl = ConfigItem(
        "Update", "AdapterRepoUrl", "https://raw.githubusercontent.com/sunfish1728/GachaPerfectAttendance/main/adapter-repo/index.json")
    appRepo = ConfigItem("Update", "AppRepo", "sunfish1728/GachaPerfectAttendance")
    autoCheckUpdate = ConfigItem("Update", "AutoCheck", True, BoolValidator())


cfg = AppConfig()
cfg.themeColor.defaultValue = DEFAULT_ACCENT
cfg.themeColor.value = DEFAULT_ACCENT


def load_config(path) -> None:
    qconfig.load(str(path), cfg)
    setTheme(cfg.themeMode.value, lazy=True)
    setThemeColor(cfg.themeColor.value, lazy=True)


class HotkeyCard(SettingCard):
    """熱鍵設定：輸入框＋套用；格式錯誤或被占用時顯示訊息。"""

    applied = Signal(str)

    def __init__(self, parent=None):
        super().__init__(FIF.CANCEL, "緊急停止熱鍵", "任何時候按下都會終止目前腳本並還原系統狀態（不執行關機等後置動作）", parent)
        self.edit = LineEdit(self)
        self.edit.setFixedWidth(180)
        self.edit.setPlaceholderText("例如 Ctrl+Shift+F12")
        self.edit.setText(_format(cfg.hotkey.value))
        btn = PushButton("套用", self)
        btn.clicked.connect(self._apply)
        self.edit.returnPressed.connect(self._apply)
        self.hBoxLayout.addWidget(self.edit)
        self.hBoxLayout.addSpacing(8)
        self.hBoxLayout.addWidget(btn)
        self.hBoxLayout.addSpacing(16)

    def _apply(self) -> None:
        from ..core.hotkey import parse_hotkey

        spec = self.edit.text().strip()
        try:
            parse_hotkey(spec)
        except ValueError as e:
            InfoBar.error("熱鍵格式錯誤", str(e), parent=self.window(), position=InfoBarPosition.TOP)
            return
        cfg.set(cfg.hotkey, spec.lower().replace(" ", ""))
        self.edit.setText(_format(spec))
        self.applied.emit(spec)


def _format(spec: str) -> str:
    try:
        from ..core.hotkey import format_hotkey

        return format_hotkey(spec)
    except Exception:
        return spec


class TextActionCard(SettingCard):
    """輸入框＋按鈕的設定卡（網址、倉庫名稱等）。"""

    clicked = Signal(str)

    def __init__(self, icon, title, content, item, placeholder, button, parent=None):
        super().__init__(icon, title, content, parent)
        self.item = item
        self.edit = LineEdit(self)
        self.edit.setFixedWidth(300)
        self.edit.setPlaceholderText(placeholder)
        self.edit.setText(item.value)
        self.edit.editingFinished.connect(lambda: cfg.set(item, self.edit.text().strip()))
        btn = PushButton(button, self)
        btn.clicked.connect(self._click)
        self.hBoxLayout.addWidget(self.edit)
        self.hBoxLayout.addSpacing(8)
        self.hBoxLayout.addWidget(btn)
        self.hBoxLayout.addSpacing(16)

    def _click(self) -> None:
        cfg.set(self.item, self.edit.text().strip())
        self.clicked.emit(self.edit.text().strip())


class SettingsPage(SmoothScrollArea):
    hotkeyChanged = Signal()
    wizardRequested = Signal()

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.setObjectName("settingsPage")
        self.controller = controller
        self.setWidgetResizable(True)
        self.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        body = QWidget()
        body.setStyleSheet("background: transparent;")
        outer = QVBoxLayout(body)
        outer.setContentsMargins(32, 28, 32, 32)
        outer.addWidget(TitleLabel("設定", body))
        outer.addSpacing(8)
        host = QWidget(body)
        layout = ExpandLayout(host)
        layout.setSpacing(24)
        layout.setContentsMargins(0, 0, 0, 0)

        look = SettingCardGroup("外觀", host)
        self.themeCard = OptionsSettingCard(
            cfg.themeMode, FIF.BRUSH, "主題", "淺色、深色或跟隨系統",
            texts=["淺色", "深色", "跟隨系統"], parent=look,
        )
        self.themeCard.optionChanged.connect(lambda ci: setTheme(cfg.get(ci), save=False))
        self.colorCard = CustomColorSettingCard(cfg.themeColor, FIF.PALETTE, "主題色", "按鈕與強調元素的顏色", look)
        self.colorCard.colorChanged.connect(lambda c: setThemeColor(c))
        look.addSettingCard(self.themeCard)
        look.addSettingCard(self.colorCard)

        behavior = SettingCardGroup("行為", host)
        behavior.addSettingCard(SwitchSettingCard(
            FIF.MINIMIZE, "關閉視窗時縮到托盤", "排程與執行會在背景繼續；從托盤選單「離開」才會結束程式",
            cfg.closeToTray, behavior,
        ))

        behavior.addSettingCard(SwitchSettingCard(
            FIF.CERTIFICATE, "啟動時以系統管理員身分執行",
            "OK 系列、一條龍等腳本需要管理員權限（預設開啟）；只在程式啟動時詢問一次 UAC",
            cfg.autoElevate, behavior,
        ))

        sched = SettingCardGroup("排程", host)
        sched.addSettingCard(RangeSettingCard(
            cfg.countdownSeconds, FIF.STOP_WATCH, "開始前倒數（秒）",
            "排程開始前在右下角顯示倒數，可立即開始、延後或略過；0 = 不倒數", sched,
        ))
        presence = SettingCardGroup("在場偵測（排程到時你正在用電腦就延後）", host)
        presence.addSettingCard(SwitchSettingCard(
            FIF.PEOPLE, "啟用在場偵測", "只影響排程觸發；手動「立即執行」不受影響", cfg.presenceEnabled, presence,
        ))
        presence.addSettingCard(RangeSettingCard(
            cfg.presenceIdleMinutes, FIF.HISTORY, "幾分鐘內有鍵鼠操作視為在場", "", presence,
        ))
        presence.addSettingCard(SwitchSettingCard(
            FIF.FULL_SCREEN, "全螢幕程式在前景時視為在場", "例如正在玩其他遊戲、看影片或簡報",
            cfg.presenceFullscreen, presence,
        ))
        presence.addSettingCard(RangeSettingCard(
            cfg.presenceDeferMinutes, FIF.DATE_TIME, "每次延後（分鐘）", "", presence,
        ))
        presence.addSettingCard(RangeSettingCard(
            cfg.presenceMaxDefers, FIF.SYNC, "最多延後次數", "超過後不再延後，直接進入倒數", presence,
        ))
        protect = SettingCardGroup("緊急停止", host)
        protect.addSettingCard(SwitchSettingCard(
            FIF.CANCEL, "啟用緊急停止熱鍵", "", cfg.hotkeyEnabled, protect,
        ))
        self.hotkeyCard = HotkeyCard(protect)
        self.hotkeyCard.applied.connect(lambda *_: self.hotkeyChanged.emit())
        cfg.hotkeyEnabled.valueChanged.connect(lambda *_: self.hotkeyChanged.emit())
        protect.addSettingCard(self.hotkeyCard)

        upd = SettingCardGroup("更新與適配器", host)
        repoCard = TextActionCard(
            FIF.CLOUD_DOWNLOAD, "適配器倉庫", "腳本改版時，從倉庫取得新的接法定義（只含設定檔，不含程式碼）",
            cfg.adapterRepoUrl, "index.json 的網址（https:// 或 file://）", "檢查更新", upd,
        )
        repoCard.clicked.connect(self._check_adapters)
        upd.addSettingCard(repoCard)
        appCard = TextActionCard(
            FIF.UPDATE, "本程式更新", "從 GitHub Release 檢查新版本", cfg.appRepo, "GitHub 倉庫，例如 owner/repo", "檢查", upd,
        )
        appCard.clicked.connect(self._check_app)
        upd.addSettingCard(appCard)
        upd.addSettingCard(SwitchSettingCard(
            FIF.SYNC, "啟動時自動檢查更新", "有設定倉庫時才會檢查；沒有新版本時不打擾", cfg.autoCheckUpdate, upd,
        ))
        wiz = PrimaryPushSettingCard("開啟嚮導", FIF.ROBOT, "首次使用嚮導", "重新掃描已安裝的腳本並建立任務鏈", upd)
        wiz.clicked.connect(self.wizardRequested)
        upd.addSettingCard(wiz)

        about = SettingCardGroup(f"關於（版本 {__version__}）", host)
        folder = PrimaryPushSettingCard("開啟資料夾", FIF.FOLDER, "資料位置",
                                        str(controller.paths.root), about)
        folder.clicked.connect(self._open_root)
        about.addSettingCard(folder)
        info = PrimaryPushSettingCard("重新載入", FIF.SYNC, "適配器", self._adapter_text(), about)
        self._adapterCard = info
        info.clicked.connect(lambda: self._reload_adapters(info))
        about.addSettingCard(info)

        for g in (look, behavior, sched, presence, protect, upd, about):
            layout.addWidget(g)
        outer.addWidget(host)
        outer.addStretch(1)
        self.setWidget(body)

    def _check_adapters(self, url: str) -> None:
        from .update_ui import AdapterUpdateDialog

        if not url:
            InfoBar.warning("請先填寫網址", "適配器倉庫 index.json 的網址", parent=self.window(), position=InfoBarPosition.TOP)
            return
        AdapterUpdateDialog(self.controller, url, self.window()).exec()
        self._adapterCard.setContent(self._adapter_text())

    def _check_app(self, repo: str) -> None:
        from .update_ui import check_app_update

        check_app_update(repo, self.window())

    def _adapter_text(self) -> str:
        reg = self.controller.registry
        text = f"已載入 {len(reg.infos)} 個：" + "、".join(i.name for i in reg.infos.values())
        if reg.errors:
            text += f"（{len(reg.errors)} 個檔案有誤）"
        return text

    def _reload_adapters(self, card) -> None:
        from ..core.registry import AdapterRegistry

        reg = AdapterRegistry()
        reg.load_dir(self.controller.paths.adapters)
        self.controller.registry.infos = reg.infos
        self.controller.registry.errors = reg.errors
        card.setContent(self._adapter_text())

    def _open_root(self) -> None:
        import os

        os.startfile(self.controller.paths.root)
