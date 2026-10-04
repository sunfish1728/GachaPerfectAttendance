"""全域緊急停止熱鍵；訊息佇列與註冊都由專用執行緒持有。"""

from __future__ import annotations

import ctypes
import logging
import threading
from ctypes import wintypes as w
from collections.abc import Callable

log = logging.getLogger("gachahub")
MOD_NOREPEAT = 0x4000
WM_HOTKEY, WM_QUIT = 0x0312, 0x0012
_MODS = {"ctrl": 2, "alt": 1, "shift": 4, "win": 8}
_FKEYS = {f"f{i}": 0x6F + i for i in range(1, 25)}
_KEYS = {"pause": 0x13, "scrolllock": 0x91, "insert": 0x2D, "home": 0x24,
         "end": 0x23, "pageup": 0x21, "pagedown": 0x22, "esc": 0x1B,
         "delete": 0x2E, "space": 0x20, "tab": 9, "enter": 13,
         "backspace": 8, "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28}
_LABELS = {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "win": "Win",
           "scrolllock": "ScrollLock", "pageup": "PageUp", "pagedown": "PageDown"}


def parse_hotkey(spec: str) -> tuple[int, int]:
    if not isinstance(spec, str):
        raise ValueError("熱鍵必須是文字")
    parts = [s.strip().lower() for s in spec.split("+")]
    mods, keys = 0, []
    seen = set()
    for part in parts:
        if not part or part in seen:
            raise ValueError("熱鍵包含空白按鍵或重複按鍵")
        seen.add(part)
        if part in _MODS:
            mods |= _MODS[part]
        else:
            keys.append(part)
    if len(keys) != 1:
        raise ValueError("熱鍵需要且只能有一個主按鍵")
    key = keys[0]
    function = key in _FKEYS
    if function:
        vk = _FKEYS[key]
    elif len(key) == 1 and key in "abcdefghijklmnopqrstuvwxyz0123456789":
        vk = ord(key.upper())
    elif key in _KEYS:
        vk = _KEYS[key]
    else:
        raise ValueError(f"不支援的熱鍵按鍵：{key}")
    if not mods and not (function or key == "pause"):
        raise ValueError("此按鍵需要搭配 Ctrl、Alt、Shift 或 Win")
    return mods, vk


def format_hotkey(spec: str) -> str:
    mods, vk = parse_hotkey(spec)
    labels = [_LABELS[k] for k in _MODS if mods & _MODS[k]]
    if 0x70 <= vk <= 0x87:
        key = f"F{vk - 0x70 + 1}"
    elif 0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A:
        key = chr(vk)
    else:
        name = next(k for k, v in _KEYS.items() if v == vk)
        key = _LABELS.get(name, name.title())
    return "+".join([*labels, key])


class GlobalHotkey:
    timeout = 3.0

    def __init__(self, spec: str, callback: Callable[[], None]):
        self.modifiers, self.vk = parse_hotkey(spec)
        self.callback = callback
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._ready = threading.Event()
        self._stop = threading.Event()
        self._result = (False, "熱鍵尚未註冊")
        self._lock = threading.RLock()

    def start(self) -> tuple[bool, str]:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return self._result if not self._stop.is_set() else (False, "熱鍵執行緒尚未停止")
            self._ready.clear()
            self._stop.clear()
            self._thread_id = 0
            self._result = (False, "熱鍵尚未註冊")
            self._thread = threading.Thread(target=self._run, name="gachahub-hotkey", daemon=True)
            self._thread.start()
            if not self._ready.wait(self.timeout):
                self.stop()
                return False, "註冊熱鍵逾時"
            return self._result

    def _run(self) -> None:
        registered = False
        try:
            user = ctypes.windll.user32
            user.RegisterHotKey.argtypes = [w.HWND, ctypes.c_int, w.UINT, w.UINT]
            user.UnregisterHotKey.argtypes = [w.HWND, ctypes.c_int]
            user.GetMessageW.argtypes = [ctypes.POINTER(w.MSG), w.HWND, w.UINT, w.UINT]
            user.PeekMessageW.argtypes = [ctypes.POINTER(w.MSG), w.HWND, w.UINT, w.UINT, w.UINT]
            get_id = ctypes.windll.kernel32.GetCurrentThreadId
            get_id.argtypes, get_id.restype = [], w.DWORD
            self._thread_id = get_id()
            msg = w.MSG()
            user.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)  # 建立訊息佇列
            registered = bool(user.RegisterHotKey(None, 1, self.modifiers | MOD_NOREPEAT, self.vk))
            self._result = (True, "") if registered else (False, "無法註冊熱鍵，可能已被其他程式占用")
            self._ready.set()
            while registered and not self._stop.is_set():
                result = user.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if result == -1:
                    log.error("熱鍵訊息讀取失敗")
                    break
                if result == 0:
                    break
                if msg.message == WM_HOTKEY and msg.wParam == 1 and not self._stop.is_set():
                    try:
                        self.callback()
                    except Exception:
                        log.exception("緊急停止熱鍵回呼失敗")
        except Exception as exc:
            self._result = False, f"熱鍵啟動失敗：{exc}"
            log.exception("熱鍵執行緒失敗")
        finally:
            if registered:
                try:
                    ctypes.windll.user32.UnregisterHotKey(None, 1)
                except Exception:
                    log.exception("解除熱鍵失敗")
            self._ready.set()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            try:
                post = ctypes.windll.user32.PostThreadMessageW
                post.argtypes = [w.DWORD, w.UINT, w.WPARAM, w.LPARAM]
                if self._thread_id:
                    post(self._thread_id, WM_QUIT, 0, 0)
            except Exception:
                log.exception("停止熱鍵訊息傳送失敗")
            if thread is not threading.current_thread():
                thread.join(self.timeout)
                if thread.is_alive():
                    log.warning("熱鍵執行緒停止逾時")
