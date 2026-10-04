"""唯讀鍵鼠活動與前景全螢幕偵測。"""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes as w

import psutil
from pydantic import BaseModel, Field


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", w.UINT), ("dwTime", w.DWORD)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", w.DWORD), ("rcMonitor", w.RECT),
                ("rcWork", w.RECT), ("dwFlags", w.DWORD)]


def idle_seconds() -> float:
    info = LASTINPUTINFO(ctypes.sizeof(LASTINPUTINFO), 0)
    ctypes.windll.user32.GetLastInputInfo.argtypes = [ctypes.POINTER(LASTINPUTINFO)]
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        raise OSError("GetLastInputInfo 失敗")
    tick = ctypes.windll.kernel32.GetTickCount64
    tick.argtypes = []
    tick.restype = ctypes.c_ulonglong
    # 輸入時間僅保留低 32 位；相減後取低 32 位處理環繞。
    return ((tick() - info.dwTime) & 0xFFFFFFFF) / 1000.0


def fullscreen_app() -> str | None:
    user = ctypes.windll.user32
    user.GetForegroundWindow.restype = w.HWND
    user.GetClassNameW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
    user.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
    user.GetWindowRect.argtypes = [w.HWND, ctypes.POINTER(w.RECT)]
    user.MonitorFromWindow.argtypes = [w.HWND, w.DWORD]
    user.MonitorFromWindow.restype = w.HMONITOR
    user.GetMonitorInfoW.argtypes = [w.HMONITOR, ctypes.POINTER(MONITORINFO)]
    hwnd = user.GetForegroundWindow()
    if not hwnd:
        return None
    name = ctypes.create_unicode_buffer(256)
    if not user.GetClassNameW(hwnd, name, len(name)):
        raise OSError("GetClassNameW 失敗")
    if name.value in ("Progman", "WorkerW", "Shell_TrayWnd"):
        return None
    pid = w.DWORD()
    if not user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)):
        raise OSError("GetWindowThreadProcessId 失敗")
    if pid.value == os.getpid():
        return None
    rect = w.RECT()
    monitor = user.MonitorFromWindow(hwnd, 2)
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(info)
    if not (monitor and user.GetWindowRect(hwnd, ctypes.byref(rect))
            and user.GetMonitorInfoW(monitor, ctypes.byref(info))):
        raise OSError("取得視窗／螢幕範圍失敗")
    screen = info.rcMonitor
    covers = (rect.left <= screen.left and rect.top <= screen.top
              and rect.right >= screen.right and rect.bottom >= screen.bottom)
    if not covers:
        state = ctypes.c_int()
        if ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(state)) < 0:
            raise OSError("SHQueryUserNotificationState 失敗")
        covers = state.value in (2, 3, 4)  # BUSY / D3D 全螢幕 / 簡報
    return psutil.Process(pid.value).name() if covers else None


class PresencePolicy(BaseModel):
    enabled: bool = True
    idle_minutes: float = Field(default=3, ge=0, allow_inf_nan=False)
    check_fullscreen: bool = True
    defer_minutes: float = Field(default=15, ge=0, allow_inf_nan=False)
    max_defers: int = Field(default=4, ge=0)


def check(policy: PresencePolicy) -> tuple[bool, str]:
    if not policy.enabled:
        return False, ""
    errors = []
    try:
        if idle_seconds() < policy.idle_minutes * 60:
            return True, f"{policy.idle_minutes:g} 分鐘內有操作"
    except Exception as exc:
        errors.append(f"鍵鼠偵測失敗：{exc}")
    if policy.check_fullscreen:
        try:
            app = fullscreen_app()
            if app:
                return True, f"全螢幕：{app}"
        except Exception as exc:
            errors.append(f"全螢幕偵測失敗：{exc}")
    return False, "；".join(errors)
