"""前景助手：腳本把遊戲帶到前景時常被 Windows 前景鎖定擋下（SetForegroundWindow 存取被拒），
例如排程時本程式在托盤、使用者正在用別的程式。一條龍的備援是最小化再還原，遊戲視窗因此重建、
舊句柄失效而卡在「遊戲窗口未就緒」。

ForegroundKeeper 在新遊戲視窗出現後的一段時間內，由本程式把它帶到前景，並允許其他程序設定前景，
之後不再干涉，避免與使用者搶焦點。"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import logging
import time

import psutil

log = logging.getLogger(__name__)

ASFW_ANY = -1
SW_RESTORE = 9
VK_MENU = 0x12
KEYEVENTF_KEYUP = 0x0002


def _user32():
    return ctypes.windll.user32


def windows_of(pids: set[int]) -> list[int]:
    """指定程序擁有的可見頂層視窗（有標題者）。"""
    u32 = _user32()
    found: list[int] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        pid = wt.DWORD()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids and u32.IsWindowVisible(hwnd) and u32.GetWindowTextLengthW(hwnd) > 0:
            found.append(int(hwnd))
        return True

    u32.EnumWindows(cb, 0)
    return found


def game_pids(names: list[str]) -> set[int]:
    wanted = {n.lower() for n in names}
    pids = set()
    for p in psutil.process_iter(["name"]):
        if (p.info["name"] or "").lower() in wanted:
            pids.add(p.pid)
    return pids


def is_foreground(hwnd: int) -> bool:
    return int(_user32().GetForegroundWindow() or 0) == hwnd


def allow_foreground() -> None:
    """允許任何程序設定前景（只在本程式目前有前景資格時生效）。"""
    try:
        _user32().AllowSetForegroundWindow(ASFW_ANY)
    except Exception:
        pass


def force_foreground(hwnd: int) -> bool:
    """把視窗帶到前景。按下並放開 Alt 讓本程式取得「最後輸入」資格，才能通過前景鎖定。"""
    u32 = _user32()
    if not u32.IsWindow(hwnd):
        return False
    if is_foreground(hwnd):
        return True
    if u32.IsIconic(hwnd):
        u32.ShowWindow(hwnd, SW_RESTORE)
    u32.keybd_event(VK_MENU, 0, 0, 0)
    try:
        u32.SetForegroundWindow(hwnd)
        u32.BringWindowToTop(hwnd)
    finally:
        u32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
    ok = is_foreground(hwnd)
    if ok:
        u32.AllowSetForegroundWindow(ASFW_ANY)  # 讓腳本之後自己切換視窗也能成功
    return ok


class ForegroundKeeper:
    """在 tick() 中檢查遊戲視窗；新視窗出現後 window_seconds 秒內，若不在前景就帶到前景（間隔 interval 秒）。"""

    def __init__(self, process_names: list[str], window_seconds: float = 90.0, interval: float = 3.0,
                 on_log=None, clock=time.monotonic):
        self.names = [n for n in process_names if n]
        self.window_seconds = window_seconds
        self.interval = interval
        self.on_log = on_log
        self.clock = clock
        self._first_seen: dict[int, float] = {}
        self._last_try = 0.0
        self._reported: set[tuple[int, bool]] = set()

    def tick(self) -> None:
        if not self.names:
            return
        try:
            self._tick()
        except Exception:  # 輔助功能失敗不可影響腳本執行
            log.exception("前景助手失敗")

    def _tick(self) -> None:
        now = self.clock()
        hwnds = windows_of(game_pids(self.names))
        for h in hwnds:
            self._first_seen.setdefault(h, now)
        for h in hwnds:
            if now - self._first_seen[h] > self.window_seconds or is_foreground(h):
                continue
            if now - self._last_try < self.interval:
                return
            self._last_try = now
            ok = force_foreground(h)
            key = (h, ok)
            if key not in self._reported and self.on_log:
                self.on_log("已將遊戲視窗帶到前景" if ok else "嘗試將遊戲視窗帶到前景失敗，稍後重試")
                self._reported.add(key)
            return
