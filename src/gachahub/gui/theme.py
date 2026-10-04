"""介面主題：復古工業控制台風格（暖色紙感顆粒底、方角線框、左側粗條、鏽橘重點）。

做法：
- 色票分淺色／深色兩組，所有自繪元件用 color()/css() 取色。
- 在 QFluentWidgets 每份樣式表後面附加覆寫規則（方角、配色）。
- 把 QFluentWidgets 各模組裡的 QPainter 換成「圓角改直角」的子類別，並覆寫側欄、卡片等少數元件的繪製。
install() 必須在建立任何視窗之前呼叫一次。
"""

from __future__ import annotations

import random
import sys

from PySide6.QtCore import QPoint, QRect, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QImage, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import QWidget

# ---------------------------------------------------------------- 色票

LIGHT = {
    "canvas": "#F2EBE1",
    "inset": "#E8DFD2",
    "track": "#DDD2C2",
    "text": "#1F4351",
    "muted": "#5B7480",
    "border": "#1F4351",
    "border_muted": "#8197A0",
    "line": (31, 67, 81, 55),
    "action": "#B5552B",
    "action_hover": "#C26437",
    "action_press": "#994520",
    "data": "#B5552B",
    "on_action": "#FBF5EC",
    "chart2": "#B8913F",
    "hover": (31, 67, 81, 16),
    "press": (31, 67, 81, 32),
    "disabled": (31, 67, 81, 95),
    "shadow": (40, 44, 48, 30),
    "success": "#2B8A4E",
    "error": "#B3302A",
    "running": "#1F6F94",
    "warning": "#A47722",
}

DARK = {
    "canvas": "#1C1A18",
    "inset": "#242120",
    "track": "#302C29",
    "text": "#DCCDBA",
    "muted": "#9D9284",
    "border": "#DCCDBA",
    "border_muted": "#5E564E",
    "line": (220, 205, 186, 46),
    "action": "#C2602F",
    "action_hover": "#CF6E3C",
    "action_press": "#A54F22",
    "data": "#CB7E54",
    "on_action": "#160D08",
    "chart2": "#7D8C69",
    "hover": (220, 205, 186, 16),
    "press": (220, 205, 186, 30),
    "disabled": (220, 205, 186, 90),
    "shadow": (0, 0, 0, 0),
    "success": "#34C46A",
    "error": "#F2605E",
    "running": "#4AB3E6",
    "warning": "#CFA048",
}

def _dark() -> bool:
    from qfluentwidgets import isDarkTheme

    return isDarkTheme()


def tokens(dark: bool | None = None) -> dict:
    return DARK if (_dark() if dark is None else dark) else LIGHT


def color(name: str, dark: bool | None = None) -> QColor:
    v = tokens(dark)[name]
    return QColor(*v) if isinstance(v, tuple) else QColor(v)


def css(name: str, dark: bool | None = None) -> str:
    v = tokens(dark)[name]
    if isinstance(v, tuple):
        r, g, b, a = v
        return f"rgba({r}, {g}, {b}, {a})"
    return v


# ---------------------------------------------------------------- 顆粒材質

_grain_cache: dict[bool, QPixmap] = {}


def grain_pixmap(dark: bool | None = None) -> QPixmap:
    """細密、低對比的暖色顆粒（可平鋪）。"""
    dark = _dark() if dark is None else dark
    if dark in _grain_cache:
        return _grain_cache[dark]
    base = color("canvas", dark)
    size = 192
    img = QImage(size, size, QImage.Format.Format_RGB32)
    rnd = random.Random(20261005 + dark)
    amp = 5 if dark else 4
    br, bg, bb = base.red(), base.green(), base.blue()
    for y in range(size):
        for x in range(size):
            d = rnd.randint(-amp, amp)
            if rnd.random() < 0.025:  # 少量較深的纖維點
                d -= 6 if dark else 9
            img.setPixel(x, y, QColor(_clamp(br + d), _clamp(bg + d), _clamp(bb + d - (1 if d > 0 else 0))).rgb())
    pm = QPixmap.fromImage(img)
    _grain_cache[dark] = pm
    return pm


def _clamp(v: int) -> int:
    return max(0, min(255, v))


def paint_grain(painter: QPainter, rect: QRect) -> None:
    painter.drawTiledPixmap(rect, grain_pixmap(), QPoint(rect.x() % 192, rect.y() % 192))


# ---------------------------------------------------------------- 字型

FONT_FAMILIES = ["Segoe UI", "Microsoft JhengHei UI", "Microsoft JhengHei"]


def ui_font(px: int, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    f = QFont()
    f.setFamilies(FONT_FAMILIES)
    f.setPixelSize(px)
    f.setWeight(weight)
    return f


# ---------------------------------------------------------------- 樣式表覆寫

_BTN = "PushButton, ToolButton, ToggleButton, ToggleToolButton, DropDownPushButton, DropDownToolButton"
_PRIMARY = "PrimaryPushButton, PrimaryToolButton, ToggleButton:checked, ToggleToolButton:checked, PrimaryDropDownPushButton"
_SPIN = "SpinBox, DoubleSpinBox, DateEdit, DateTimeEdit, TimeEdit, CompactSpinBox, CompactDoubleSpinBox"

OVERRIDES = {
    "label": """
FluentLabelBase { color: @text; }
HyperlinkLabel { color: @action; }
""",
    "button": f"""
{_BTN} {{
    color: @text; background: @inset; border: 1px solid @border_muted; border-radius: 0px;
}}
PushButton:hover, ToolButton:hover, ToggleButton:hover, ToggleToolButton:hover,
DropDownPushButton:hover, DropDownToolButton:hover {{ background: @track; border: 1px solid @text; }}
PushButton:pressed, ToolButton:pressed, ToggleButton:pressed, ToggleToolButton:pressed {{
    color: @text; background: @track; border: 1px solid @text;
}}
PushButton:disabled, ToolButton:disabled, ToggleButton:disabled, ToggleToolButton:disabled {{
    color: @disabled; background: transparent; border: 1px solid @disabled;
}}
{_PRIMARY} {{
    color: @on_action; background-color: @action; border: 1px solid @action; border-radius: 0px;
}}
PrimaryPushButton:hover, PrimaryToolButton:hover, ToggleButton:checked:hover, ToggleToolButton:checked:hover {{
    background-color: @action_hover; border: 1px solid @action_hover;
}}
PrimaryPushButton:pressed, PrimaryToolButton:pressed, ToggleButton:checked:pressed, ToggleToolButton:checked:pressed {{
    color: @on_action; background-color: @action_press; border: 1px solid @action_press;
}}
PrimaryPushButton:disabled, PrimaryToolButton:disabled {{
    color: @disabled; background-color: @track; border: 1px solid @track;
}}
TransparentToolButton, TransparentPushButton, TransparentToggleToolButton, TransparentDropDownPushButton {{
    color: @text; background-color: transparent; border: 1px solid transparent; border-radius: 0px;
}}
TransparentToolButton:hover, TransparentPushButton:hover {{ background-color: @hover; border: 1px solid @border_muted; }}
TransparentToolButton:pressed, TransparentPushButton:pressed {{ background-color: @press; }}
PillPushButton, PillToolButton {{ border-radius: 0px; }}
HyperlinkButton {{ color: @action; border-radius: 0px; }}
""",
    "line_edit": """
LineEdit, TextEdit, PlainTextEdit, TextBrowser {
    color: @text; background-color: @inset; border: 1px solid @border_muted; border-radius: 0px;
    selection-background-color: @action; selection-color: @on_action;
}
LineEdit:hover, TextEdit:hover, PlainTextEdit:hover, TextBrowser:hover {
    background-color: @inset; border: 1px solid @text;
}
LineEdit:focus, TextEdit:focus, PlainTextEdit:focus, TextBrowser:focus {
    background-color: @inset; border: 1px solid @text;
}
LineEdit:disabled, TextEdit:disabled, PlainTextEdit:disabled, TextBrowser:disabled {
    color: @disabled; background-color: transparent; border: 1px solid @disabled;
}
#lineEditButton { border-radius: 0px; }
#lineEditButton:hover { background-color: @hover; }
""",
    "combo_box": """
ComboBox, ModelComboBox {
    color: @text; background-color: @inset; border: 1px solid @border_muted; border-radius: 0px;
}
ComboBox:hover, ModelComboBox:hover { background-color: @inset; border: 1px solid @text; }
ComboBox:pressed, ModelComboBox:pressed { color: @text; background-color: @track; border: 1px solid @text; }
ComboBox:disabled, ModelComboBox:disabled { color: @disabled; background: transparent; border: 1px solid @disabled; }
ComboBox[isPlaceholderText=true] { color: @muted; }
""",
    "spin_box": f"""
{_SPIN} {{
    color: @text; background-color: @inset; border: 1px solid @border_muted; border-radius: 0px;
    selection-background-color: @action; selection-color: @on_action;
}}
SpinBox:hover, DoubleSpinBox:hover, CompactSpinBox:hover {{ background-color: @inset; border: 1px solid @text; }}
SpinBox:focus, DoubleSpinBox:focus, CompactSpinBox:focus {{ background-color: @inset; border: 1px solid @text; }}
SpinBox:disabled, DoubleSpinBox:disabled, CompactSpinBox:disabled {{
    color: @disabled; background-color: transparent; border: 1px solid @disabled;
}}
SpinButton {{ border-radius: 0px; }}
SpinButton:hover {{ background-color: @hover; }}
""",
    "check_box": """
CheckBox { color: @text; }
CheckBox:disabled { color: @disabled; }
CheckBox::indicator { border-radius: 0px; }
""",
    "switch_button": """
SwitchButton > QLabel { color: @text; }
SwitchButton > QLabel:disabled { color: @disabled; }
""",
    "setting_card": """
QLabel { color: @text; }
QLabel#contentLabel, RangeSettingCard > QLabel#valueLabel { color: @muted; }
QLabel:disabled, QLabel#contentLabel:disabled { color: @disabled; }
QPushButton {
    color: @text; background: @inset; border: 1px solid @border_muted; border-radius: 0px;
}
QPushButton:hover { background: @track; border: 1px solid @text; }
QPushButton:pressed { color: @text; background: @track; }
QPushButton:disabled { color: @disabled; background: transparent; border: 1px solid @disabled; }
#primaryButton { color: @on_action; background-color: @action; border: 1px solid @action; }
#primaryButton:hover { background-color: @action_hover; border: 1px solid @action_hover; }
#primaryButton:pressed { color: @on_action; background-color: @action_press; border: 1px solid @action_press; }
ColorPickerButton { border-radius: 0px; }
""",
    "setting_card_group": """
SettingCardGroup > QLabel { color: @action; font-weight: bold; }
""",
    "expand_setting_card": """
ExpandSettingCard { border-radius: 0px; }
#view { background: @inset; border-radius: 0px; }
QLabel#titleLabel { color: @text; }
QLabel#contentLabel { color: @muted; }
QPushButton { color: @text; background: @inset; border: 1px solid @border_muted; border-radius: 0px; }
QPushButton:hover { background: @track; border: 1px solid @text; }
""",
    "card_widget": """
HeaderCardWidget #headerLabel { color: @text; }
""",
    "dialog": """
QDialog { background-color: @canvas; }
#buttonGroup { background-color: @inset; border-top: 1px solid @border_muted; }
MessageBoxBase #buttonGroup, MessageBox #buttonGroup { border-bottom-left-radius: 0px; border-bottom-right-radius: 0px; }
#centerWidget { border: 1px solid @text; border-left: 9px solid @text; border-radius: 0px; background-color: @canvas; }
QLabel { color: @text; }
#contentLabel { selection-background-color: @action; selection-color: @on_action; }
QLabel#windowTitleLabel { background-color: @inset; }
#cancelButton {
    color: @text; background: @inset; border: 1px solid @border_muted; border-radius: 0px;
}
#cancelButton:hover { background: @track; border: 1px solid @text; }
#cancelButton:pressed { color: @text; background: @track; }
QWidget#windowMask { background-color: @mask; }
""",
    "message_dialog": """
QWidget { background-color: @canvas; border: 1px solid @text; }
QWidget#windowMask { background-color: @mask; border: none; }
QLabel { color: @text; }
""",
    "info_bar": """
InfoBar { border: 1px solid @border_muted; border-left: 4px solid @action; border-radius: 0px; background-color: @inset; }
InfoBar[type="Info"], InfoBar[type="Success"], InfoBar[type="Warning"], InfoBar[type="Error"] { background-color: @inset; }
InfoBar[type="Success"] { border-left: 4px solid @success; }
InfoBar[type="Error"] { border-left: 4px solid @error; }
InfoBar[type="Warning"] { border-left: 4px solid @warning; }
#titleLabel, #contentLabel { color: @text; }
""",
    "menu": """
MenuActionListWidget { border: 1px solid @text; border-radius: 0px; background-color: @canvas; }
MenuActionListWidget::item { border-radius: 0px; color: @text; margin-left: 4px; margin-right: 4px; }
MenuActionListWidget::item:disabled { border-radius: 0px; color: @disabled; }
MenuActionListWidget::item:hover { background-color: @hover; }
MenuActionListWidget::item:selected { background-color: @action; color: @on_action; }
MenuActionListWidget::item:selected:active { background-color: @action_press; color: @on_action; }
#completerListWidget[dropDown=true], #commandListWidget[dropDown=true][long=true],
#commandListWidget[dropDown=false][long=true] { border-radius: 0px; }
""",
    "tool_tip": """
ToolTip { border-radius: 0px; }
ToolTip > #container { border: 1px solid @text; background-color: @canvas; border-radius: 0px; }
QLabel { color: @text; }
""",
    "time_picker": """
ScrollButton { background-color: @canvas; border-radius: 0px; }
CycleListWidget { border-radius: 0px; }
CycleListWidget::item { color: @text; border-radius: 0px; }
CycleListWidget::item:hover, CycleListWidget::item:selected { background-color: @hover; }
PickerPanel > #view { background-color: @canvas; border: 1px solid @text; border-radius: 0px; }
SeparatorWidget { background-color: @border_muted; }
PickerBase { color: @text; background: @inset; border: 1px solid @border_muted; border-radius: 0px; }
PickerBase:hover { background: @inset; border: 1px solid @text; }
PickerBase:pressed { background: @track; }
PickerBase:disabled { color: @disabled; background: transparent; border: 1px solid @disabled; }
#pickerButton { color: @muted; }
#pickerButton[hasValue=true]:enabled, #pickerButton[enter=true]:enabled { color: @text; }
#pickerButton[hasBorder=true]:enabled { border-right: 1px solid @border_muted; }
""",
    "calendar_picker": """
CalendarPicker { color: @text; background: @inset; border: 1px solid @border_muted; border-radius: 0px; }
CalendarPicker:hover { background: @inset; border: 1px solid @text; }
CalendarPicker[hasDate=false] { color: @muted; }
""",
    "list_view": """
ListView, ListWidget { color: @text; }
""",
    "table_view": """
QTableView { color: @text; }
QTableView[isBorderVisible=true] { border: 1px solid @border_muted; }
QHeaderView::section { color: @muted; border: 1px solid @line; }
QHeaderView::section:horizontal { border-left: none; border-top: none; border-bottom: 1px solid @text; }
QTableCornerButton::section { border: none; }
""",
    "pivot": """
PivotItem { color: @muted; }
PivotItem[isSelected=true], PivotItem[isSelected=true]:hover { color: @text; }
SegmentedItem, SegmentedItem[isSelected=true] { border-radius: 0px; }
""",
    "fluent_window": """
StackedWidget {
    border: none; border-left: 1px solid @border_muted; border-top: 1px solid @border_muted;
    border-top-left-radius: 0px; background-color: transparent;
}
FluentTitleBar > QLabel#titleLabel { color: @text; }
MinimizeButton, MaximizeButton {
    qproperty-normalColor: @text; qproperty-hoverColor: @text; qproperty-pressedColor: @text;
    qproperty-hoverBackgroundColor: @hover; qproperty-pressedBackgroundColor: @press;
}
CloseButton { qproperty-normalColor: @text; }
""",
    "navigation_interface": """
NavigationPanel[menu=false], NavigationPanel[menu=true] { border-radius: 0px; background-color: transparent; border: none; }
""",
    "state_tool_tip": """
StateToolTip { border-radius: 0px; }
""",
    "teaching_tip": """
""",
}

_EXTRA = {
    "mask": ((242, 235, 225, 150), (14, 12, 11, 150)),
}


def _render_override(name: str, dark: bool) -> str:
    qss = OVERRIDES.get(name, "")
    if not qss:
        return ""
    t = tokens(dark)
    # 長的鍵先替換，避免 @action 先吃掉 @action_hover
    for key in sorted(list(t) + list(_EXTRA), key=len, reverse=True):
        if key in _EXTRA:
            r, g, b, a = _EXTRA[key][1 if dark else 0]
            value = f"rgba({r}, {g}, {b}, {a})"
        else:
            value = css(key, dark)
        qss = qss.replace("@" + key, value)
    return qss


# ---------------------------------------------------------------- 繪製覆寫

class _SquarePainter(QPainter):
    """把圓角矩形一律畫成直角（介面以方角為預設）。"""

    def drawRoundedRect(self, *args, **kwargs):  # noqa: N802
        rect = args[0]
        if isinstance(rect, (QRect, QRectF)):
            return self.drawRect(rect)
        x, y, w, h = args[:4]
        return self.drawRect(QRectF(x, y, w, h))


def _patch_painters() -> None:
    for name, mod in list(sys.modules.items()):
        if name.startswith("qfluentwidgets") and getattr(mod, "QPainter", None) is QPainter:
            mod.QPainter = _SquarePainter


def _nav_paint(self, e) -> None:
    """側欄項目：選中為整塊鏽橘矩形，其餘為次要文字色。"""
    from qfluentwidgets.common.icon import drawIcon

    p = QPainter(self)
    p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing
                     | QPainter.RenderHint.SmoothPixmapTransform)
    p.setPen(Qt.PenStyle.NoPen)
    if not self.isEnabled():
        p.setOpacity(0.4)
    m = self._margins()
    pl, pr = m.left(), m.right()
    rect = self.rect().adjusted(2, 1, -2, -1)
    selected = self._canDrawIndicator()
    hovered = (self.isEnter or self.isAboutSelected) and self.isEnabled()
    if selected:
        p.setBrush(color("action_press" if self.isPressed else "action"))
        p.drawRect(rect)
        fg = color("on_action")
    else:
        if hovered:
            p.setBrush(color("press" if self.isPressed else "hover"))
            p.drawRect(rect)
        fg = color("text") if hovered else color("muted")
    drawIcon(self._icon, p, QRectF(11.5 + pl, 10, 16, 16), fill=fg.name())
    if self.isCompacted:
        return
    f = self.font()
    f.setWeight(QFont.Weight.DemiBold if selected else QFont.Weight.Normal)
    p.setFont(f)
    p.setPen(fg)
    left = 44 + pl if not self.icon().isNull() else pl + 16
    p.drawText(QRectF(left, 0, self.width() - 13 - left - pr, self.height()), Qt.AlignmentFlag.AlignVCenter, self.text())


def _card_paint(self, e) -> None:
    """卡片：透明底（露出顆粒）、細框；property rail=True 時加左側粗條與鉚點。"""
    p = QPainter(self)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    r = self.rect().adjusted(0, 0, -1, -1)
    from qfluentwidgets import ElevatedCardWidget, SimpleCardWidget

    rail = bool(self.property("rail"))
    clickable = isinstance(self, ElevatedCardWidget) or not isinstance(self, SimpleCardWidget)
    hover = clickable and getattr(self, "isHover", False)
    pressed = clickable and getattr(self, "isPressed", False)
    if not _dark() and rail:  # 淺色主題的主要面板有淡淡的右下陰影
        p.fillRect(r.adjusted(3, 3, 1, 1), color("shadow"))
        r = r.adjusted(0, 0, -2, -2)
    p.fillRect(r, color("canvas"))
    paint_grain(p, r)
    if hover or pressed:
        p.fillRect(r, color("press" if pressed else "hover"))
    edge = color("border") if (rail or hover) else color("border_muted")
    p.setPen(edge)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRect(r)
    if rail:
        p.fillRect(QRect(r.x(), r.y(), 9, r.height() + 1), color("border"))
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color("canvas"))
        p.drawEllipse(QRectF(r.x() + 2.5, r.y() + 5, 4, 4))


def _setting_card_paint(self, e) -> None:
    p = QPainter(self)
    r = self.rect().adjusted(0, 0, -1, -1)
    p.fillRect(r, color("inset"))
    p.setPen(color("border_muted"))
    p.drawRect(r)


def _switch_paint(self, e) -> None:
    """開關：直角矩形軌道＋方形滑塊。"""
    p = QPainter(self)
    r = self.rect().adjusted(1, 1, -2, -2)
    on = self.isChecked()
    if not self.isEnabled():
        p.setPen(color("disabled"))
        p.setBrush(color("track") if on else Qt.BrushStyle.NoBrush)
    elif on:
        p.setPen(color("action"))
        p.setBrush(color("action_hover" if self.isHover else "action"))
    else:
        p.setPen(color("text") if self.isHover else color("border_muted"))
        p.setBrush(color("inset"))
    p.drawRect(r)
    knob = color("on_action") if on else (color("text") if self.isEnabled() else color("disabled"))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(knob)
    p.drawRect(QRectF(self.sliderX, 5, 12, 12))


def _progress_paint(self, e) -> None:
    p = QPainter(self)
    p.fillRect(self.rect(), color("track"))
    if self.minimum() >= self.maximum():
        return
    w = int(self.val / (self.maximum() - self.minimum()) * self.width())
    p.fillRect(QRect(0, 0, w, self.height()), self.barColor() if self.isEnabled() else color("disabled"))


def _checkbox_border(self):
    if not self.isEnabled():
        return color("disabled")
    if self.isChecked():
        return color("action")
    return color("text") if (self.isHover or self.isPressed) else color("border_muted")


def _checkbox_background(self):
    if not self.isEnabled():
        return color("track") if self.isChecked() else QColor(0, 0, 0, 0)
    if self.isChecked():
        return color("action_press" if self.isPressed else ("action_hover" if self.isHover else "action"))
    return color("inset")


def _table_background(self, painter, option, index) -> None:
    painter.drawRect(option.rect.adjusted(0, 0, 0, -1))


def _list_background(self, painter, option, index) -> None:
    painter.drawRect(option.rect)


def _table_indicator(self, painter, option, index) -> None:
    painter.setBrush(color("action"))
    painter.drawRect(QRect(option.rect.x(), option.rect.y(), 3, option.rect.height()))


def _expand_border_paint(self, e) -> None:
    p = QPainter(self)
    p.setPen(color("border_muted"))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRect(self.rect().adjusted(0, 0, -1, -1))
    ch = self.parent().card.height()
    if ch < self.height():
        p.drawLine(0, ch, self.width() - 1, ch)


def _patch_classes() -> None:
    from qfluentwidgets.common.icon import FluentIcon, FluentIconBase, Theme
    from qfluentwidgets.common.style_sheet import FluentStyleSheet
    from qfluentwidgets.components.navigation.navigation_widget import NavigationPushButton
    from qfluentwidgets.components.settings.expand_setting_card import ExpandBorderWidget, HeaderSettingCard
    from qfluentwidgets.components.settings.setting_card import SettingCard
    from qfluentwidgets.components.widgets.label import FluentLabelBase
    from qfluentwidgets.components.widgets.card_widget import CardWidget, SimpleCardWidget
    from qfluentwidgets.components.widgets.check_box import CheckBox
    from qfluentwidgets.components.widgets.list_view import ListItemDelegate
    from qfluentwidgets.components.widgets.progress_bar import ProgressBar
    from qfluentwidgets.components.widgets.switch_button import Indicator
    from qfluentwidgets.components.widgets.table_view import TableItemDelegate

    # 每份樣式表後面附加覆寫規則
    original_content = FluentStyleSheet.content

    def content(self, theme=Theme.AUTO):
        from qfluentwidgets import qconfig

        dark = (qconfig.theme if theme == Theme.AUTO else theme) == Theme.DARK
        return original_content(self, theme) + "\n" + _render_override(self.value, dark)

    FluentStyleSheet.content = content

    # Fluent 圖示預設改用結構色（深青綠／暖米灰）
    original_render = FluentIconBase.render

    def render(self, painter, rect, theme=Theme.AUTO, indexes=None, **attributes):
        if theme == Theme.AUTO and not attributes and isinstance(self, FluentIcon):
            attributes = {"fill": color("text").name()}
        return original_render(self, painter, rect, theme, indexes, **attributes)

    FluentIconBase.render = render

    NavigationPushButton.paintEvent = _nav_paint
    CardWidget.paintEvent = _card_paint
    SimpleCardWidget.paintEvent = _card_paint
    SettingCard.paintEvent = _setting_card_paint
    HeaderSettingCard.paintEvent = _setting_card_paint
    ExpandBorderWidget.paintEvent = _expand_border_paint

    # 標籤預設的黑／白字改為主題結構色；自訂顏色維持不變
    original_text_color = FluentLabelBase.setTextColor
    black, white = QColor(0, 0, 0), QColor(255, 255, 255)

    def set_text_color(self, light=None, dark=None):
        light = color("text", False) if light is None or QColor(light) == black else light
        dark = color("text", True) if dark is None or QColor(dark) == white else dark
        original_text_color(self, light, dark)

    FluentLabelBase.setTextColor = set_text_color
    Indicator.paintEvent = _switch_paint
    ProgressBar.paintEvent = _progress_paint
    CheckBox._borderColor = _checkbox_border
    CheckBox._backgroundColor = _checkbox_background
    TableItemDelegate._drawBackground = _table_background
    TableItemDelegate._drawIndicator = _table_indicator
    ListItemDelegate._drawBackground = _list_background
    ListItemDelegate._drawIndicator = _table_indicator

    original_init_option = TableItemDelegate.initStyleOption

    def init_option(self, option, index):
        original_init_option(self, option, index)
        if index.data(Qt.ItemDataRole.ForegroundRole) is None:
            option.palette.setColor(QPalette.ColorRole.Text, color("text"))
            option.palette.setColor(QPalette.ColorRole.HighlightedText, color("text"))

    TableItemDelegate.initStyleOption = init_option


_installed = False


def install() -> None:
    """套用主題（重複呼叫無害）。需在 QApplication 建立後、任何視窗建立前呼叫。"""
    global _installed
    if _installed:
        return
    _installed = True
    import qfluentwidgets  # noqa: F401  確保所有模組已載入再替換 QPainter
    from qfluentwidgets import qconfig, setThemeColor

    _patch_classes()
    _patch_painters()
    qconfig.fontFamilies.value = list(FONT_FAMILIES)
    setThemeColor(LIGHT["action"], lazy=True)

    def on_theme(*_):
        setThemeColor(color("action").name(), lazy=True)

    qconfig.themeChanged.connect(on_theme)


def apply_window_background(window: QWidget) -> None:
    """FluentWindow：關閉 Mica，用顆粒底取代純色背景。"""
    window.setMicaEffectEnabled(False)
    window.setCustomBackgroundColor(QColor(LIGHT["canvas"]), QColor(DARK["canvas"]))


def paint_window(window: QWidget) -> None:
    p = QPainter(window)
    p.fillRect(window.rect(), color("canvas"))
    paint_grain(p, window.rect())


def grain_brush() -> QBrush:
    return QBrush(grain_pixmap())
