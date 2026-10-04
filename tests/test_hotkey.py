import ctypes
import queue
import threading
from types import SimpleNamespace

import pytest

from gachahub.core import hotkey as h
from test_presence import Fn


@pytest.mark.parametrize("spec,expected,label", [
    (" ctrl + SHIFT + f12 ", (6, 0x7B), "Ctrl+Shift+F12"),
    ("alt+pause", (1, 0x13), "Alt+Pause"),
    ("ctrl+alt+s", (3, 83), "Ctrl+Alt+S"),
    ("win+0", (8, 48), "Win+0"), ("pause", (0, 19), "Pause"),
    *[(f"f{i}", (0, 0x6F+i), f"F{i}") for i in range(1, 25)],
    *[(f"ctrl+{c}", (2, ord(c)), f"Ctrl+{c}") for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"],
    *[(f"shift+{k}", (4, vk), "Shift+" + h._LABELS.get(k, k.title())) for k, vk in h._KEYS.items()],
])
def test_parse_format(spec, expected, label):
    assert h.parse_hotkey(spec) == expected
    assert h.format_hotkey(spec) == label


@pytest.mark.parametrize("spec", ["", "ctrl", "ctrl+shift", "s", "esc", "f0", "f25", "ctrl++s",
                                      "ctrl+s+s", "ctrl+ctrl+s", "ctrl+s+a", "meta+s", "ctrl+未知",
                                      "ctrl+f²", "f01", None])
def test_invalid(spec):
    with pytest.raises(ValueError):
        h.parse_hotkey(spec)


@pytest.fixture
def api(monkeypatch):
    messages = queue.Queue()
    events = []
    def register(*args):
        events.append(("register", threading.get_ident(), args))
        return 1
    def get(ptr, *args):
        kind = messages.get(timeout=2)
        ptr._obj.message, ptr._obj.wParam = kind, 1
        return 0 if kind == h.WM_QUIT else 1
    def peek(*args):
        # 真實的訊息佇列隨執行緒結束消失，新執行緒不會收到舊 WM_QUIT。
        while not messages.empty():
            messages.get_nowait()
        return 0
    user = SimpleNamespace(RegisterHotKey=Fn(register),
        UnregisterHotKey=Fn(lambda *args: events.append(("unregister", threading.get_ident())) or 1),
        PeekMessageW=Fn(peek), GetMessageW=Fn(get),
        PostThreadMessageW=Fn(lambda tid, kind, *args: messages.put(kind) or 1))
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(user32=user,
        kernel32=SimpleNamespace(GetCurrentThreadId=Fn(threading.get_ident))))
    return user, messages, events


def test_lifecycle_and_callback_exception(api, caplog):
    _, messages, events = api
    called = threading.Event()
    callback_threads = []
    def callback():
        callback_threads.append(threading.get_ident())
        called.set()
        raise RuntimeError("callback error")
    hotkey = h.GlobalHotkey("ctrl+f12", callback)
    for _ in range(2):
        assert hotkey.start() == (True, "")
        assert hotkey.start() == (True, "")
        called.clear()
        messages.put(h.WM_HOTKEY)
        assert called.wait(1)
        called.clear()
        messages.put(h.WM_HOTKEY)
        assert called.wait(1)  # 回呼例外後同一個執行緒仍可接收下一次熱鍵
        hotkey.stop()
        assert not hotkey._thread.is_alive()
    hotkey.stop()
    assert len([e for e in events if e[0] == "unregister"]) == 2
    assert callback_threads[0] != threading.get_ident()
    assert events[0][2][2] & h.MOD_NOREPEAT
    assert "回呼失敗" in caplog.text


def test_registration_failure(api):
    user, _, events = api
    user.RegisterHotKey = Fn(lambda *args: 0)
    hotkey = h.GlobalHotkey("f12", lambda: None)
    ok, reason = hotkey.start()
    hotkey.stop()
    assert not ok and "占用" in reason
    assert not events


def test_registration_timeout(api):
    user, _, events = api
    release = threading.Event()
    user.RegisterHotKey = Fn(lambda *args: release.wait(1) or 1)
    hotkey = h.GlobalHotkey("f12", lambda: None)
    hotkey.timeout = 0.02
    try:
        assert hotkey.start() == (False, "註冊熱鍵逾時")
    finally:
        release.set()
        hotkey._thread.join(1)
    assert not hotkey._thread.is_alive()
    assert events[-1][0] == "unregister"
