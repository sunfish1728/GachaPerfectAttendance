from gachahub.core import foreground as fg


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def setup(monkeypatch, windows, front, results):
    calls = []
    monkeypatch.setattr(fg, "game_pids", lambda names: {1})
    monkeypatch.setattr(fg, "windows_of", lambda pids: list(windows))
    monkeypatch.setattr(fg, "is_foreground", lambda h: h in front)

    def force(h):
        calls.append(h)
        ok = results.pop(0) if results else True
        if ok:
            front.add(h)
        return ok

    monkeypatch.setattr(fg, "force_foreground", force)
    return calls


def test_brings_new_window_forward_once(monkeypatch):
    clock, logs, front = Clock(), [], set()
    calls = setup(monkeypatch, [11], front, [True])
    k = fg.ForegroundKeeper(["Game.exe"], on_log=logs.append, clock=clock)
    k.tick()
    clock.t += 5
    k.tick()
    assert calls == [11]
    assert logs == ["已將遊戲視窗帶到前景"]


def test_retries_with_interval_then_stops_after_window(monkeypatch):
    clock, logs, front = Clock(), [], set()
    calls = setup(monkeypatch, [11], front, [False] * 100)
    k = fg.ForegroundKeeper(["Game.exe"], window_seconds=10, interval=3, on_log=logs.append, clock=clock)
    for _ in range(30):
        k.tick()
        clock.t += 1
    assert 3 <= len(calls) <= 4  # 只在出現後 10 秒內、每 3 秒一次
    assert logs == ["嘗試將遊戲視窗帶到前景失敗，稍後重試"]


def test_does_not_fight_user_after_window(monkeypatch):
    clock, front = Clock(), set()
    calls = setup(monkeypatch, [11], front, [True])
    k = fg.ForegroundKeeper(["Game.exe"], window_seconds=10, clock=clock)
    k.tick()
    front.clear()  # 使用者切走
    clock.t += 60
    k.tick()
    assert calls == [11]


def test_no_names_and_errors_are_harmless(monkeypatch):
    fg.ForegroundKeeper([]).tick()
    monkeypatch.setattr(fg, "game_pids", lambda names: (_ for _ in ()).throw(RuntimeError("x")))
    fg.ForegroundKeeper(["Game.exe"]).tick()  # 不可拋出


def test_windows_of_runs_on_real_desktop():
    assert isinstance(fg.windows_of({0}), list)
