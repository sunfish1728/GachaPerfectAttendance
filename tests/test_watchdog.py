from gachahub.core.watchdog import StallWatch


def test_changes_and_missing_directory(tmp_path, monkeypatch):
    monkeypatch.setattr("gachahub.core.watchdog.time.monotonic", lambda: 100)
    directory = tmp_path / "logs"
    watch = StallWatch([directory], 1)
    assert watch.check(130) == 30
    assert watch.check(161) == 61
    directory.mkdir()
    child = directory / "nested"
    child.mkdir()
    path = child / "a.log"
    path.write_text("abc")
    assert watch.check(170) == 0
    assert watch.check(180) == 10
    path.write_text("abcd")
    assert watch.check(190) == 0
    path.unlink()
    assert watch.check(200) == 0


def test_file_mtime_and_disabled(tmp_path, monkeypatch):
    import os
    clock = [0]
    monkeypatch.setattr("gachahub.core.watchdog.time.monotonic", lambda: clock[0])
    path = tmp_path / "a.log"
    path.write_text("a")
    watch = StallWatch([path], 1)
    clock[0] = 60
    assert watch.stalled() and watch.idle_for() == 60
    os.utime(path, ns=(1_000_000_000, 1_000_000_000))
    assert watch.idle_for() == 0
    for minutes in (0, -1):
        disabled = StallWatch([path], minutes)
        clock[0] += 1000
        assert disabled.check() is None
        assert not disabled.stalled() and disabled.idle_for() == 0
