import ctypes
from types import SimpleNamespace

import pytest

from gachahub.core import presence as p


class Fn:
    def __init__(self, call):
        self.call = call

    def __call__(self, *args):
        return self.call(*args)


@pytest.fixture
def api(monkeypatch):
    user = SimpleNamespace()
    kernel = SimpleNamespace(GetTickCount64=Fn(lambda: (1 << 32) + 2000))
    shell = SimpleNamespace(SHQueryUserNotificationState=Fn(lambda ptr: setattr(ptr._obj, "value", 1) or 0))
    def last(ptr):
        ptr._obj.dwTime = 0xFFFFFC18  # 環繞前 1000 ms
        return 1
    user.GetLastInputInfo = Fn(last)
    user.GetForegroundWindow = Fn(lambda: 10)
    user.GetClassNameW = Fn(lambda hwnd, buf, size: setattr(buf, "value", "GameWindow") or 10)
    user.GetWindowThreadProcessId = Fn(lambda hwnd, ptr: setattr(ptr._obj, "value", 123) or 1)
    user.MonitorFromWindow = Fn(lambda *args: 20)
    def rect(hwnd, ptr):
        ptr._obj.left, ptr._obj.top, ptr._obj.right, ptr._obj.bottom = -1920, 0, 0, 1080
        return 1
    def monitor(hwnd, ptr):
        return rect(hwnd, SimpleNamespace(_obj=ptr._obj.rcMonitor))
    user.GetWindowRect, user.GetMonitorInfoW = Fn(rect), Fn(monitor)
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(user32=user, kernel32=kernel, shell32=shell))
    monkeypatch.setattr(p.psutil, "Process", lambda pid: SimpleNamespace(name=lambda: "game.exe"))
    return user, kernel, shell


def test_idle_wrap_and_failure(api):
    user, _, _ = api
    assert p.idle_seconds() == 3
    user.GetLastInputInfo = Fn(lambda ptr: 0)
    with pytest.raises(OSError):
        p.idle_seconds()
    present, reason = p.check(p.PresencePolicy(check_fullscreen=False))
    assert not present and "失敗" in reason


def test_fullscreen_geometry_and_shell(api):
    user, _, shell = api
    assert p.fullscreen_app() == "game.exe"
    user.GetWindowRect = Fn(lambda hwnd, ptr: 1)  # 普通視窗
    assert p.fullscreen_app() is None
    for state in (2, 3, 4):
        shell.SHQueryUserNotificationState = Fn(lambda ptr: setattr(ptr._obj, "value", state) or 0)
        assert p.fullscreen_app() == "game.exe"


@pytest.mark.parametrize("name", ["Progman", "WorkerW", "Shell_TrayWnd"])
def test_desktop_excluded(api, name):
    api[0].GetClassNameW = Fn(lambda hwnd, buf, size: setattr(buf, "value", name) or 1)
    assert p.fullscreen_app() is None


def test_own_process_and_policy(api, monkeypatch):
    api[0].GetWindowThreadProcessId = Fn(lambda hwnd, ptr: setattr(ptr._obj, "value", p.os.getpid()) or 1)
    assert p.fullscreen_app() is None
    assert p.check(p.PresencePolicy(enabled=False)) == (False, "")
    assert p.check(p.PresencePolicy(idle_minutes=2)) == (True, "2 分鐘內有操作")
    monkeypatch.setattr(p, "idle_seconds", lambda: 1000)
    monkeypatch.setattr(p, "fullscreen_app", lambda: "game.exe")
    assert p.check(p.PresencePolicy()) == (True, "全螢幕：game.exe")
    assert p.check(p.PresencePolicy(check_fullscreen=False)) == (False, "")


def test_fullscreen_api_failure(api, monkeypatch):
    monkeypatch.setattr(p, "idle_seconds", lambda: 1000)
    api[0].GetWindowRect = Fn(lambda *args: 0)
    present, reason = p.check(p.PresencePolicy())
    assert not present and "失敗" in reason


def test_idle_readonly_smoke():
    assert p.idle_seconds() >= 0
