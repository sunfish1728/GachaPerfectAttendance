from datetime import datetime as D

import pytest

from gachahub.core.schedule import Schedule, ScheduleKind, ScheduleStore, check_due, next_fire, upcoming


def schedule(**kwargs):
    return Schedule(chain="每日任務", **kwargs)


@pytest.mark.parametrize("kind,time,after,expected,extra", [
    ("daily", "00:01", "2026-12-31T23:59", "2027-01-01T00:01", {}),
    ("daily", "04:30", "2026-01-31T04:30", "2026-02-01T04:30", {}),
    ("weekly", "04:30", "2026-10-04T12:00", "2026-10-05T04:30", {"weekdays": [0]}),
    ("weekly", "04:30", "2026-10-05T04:30", "2026-10-12T04:30", {"weekdays": [0]}),
    ("weekly", "04:30", "2026-10-04T12:00", None, {"weekdays": []}),
    ("interval", "04:30", "2026-10-04T01:00", "2026-10-04T04:30", {"interval_minutes": 240}),
    ("interval", "04:30", "2026-10-04T00:00", "2026-10-04T00:30", {"interval_minutes": 240}),
    ("interval", "23:30", "2026-12-31T23:30", "2027-01-01T00:30", {"interval_minutes": 60}),
    ("interval", "04:30", "2026-10-04T00:00", None, {"interval_minutes": 0}),
    ("once", "04:30", "2026-10-04T00:00", "2026-10-04T04:30", {"date": "2026-10-04"}),
    ("once", "04:30", "2026-10-04T04:30", None, {"date": "2026-10-04"}),
    ("once", "04:30", "2026-10-05T00:00", None, {"date": "2026-10-04"}),
    ("once", "04:30", "2026-10-04T00:00", None, {"date": "bad"}),
])
def test_next_fire(kind, time, after, expected, extra):
    actual = next_fire(schedule(kind=kind, time=time, **extra), D.fromisoformat(after))
    assert actual == (D.fromisoformat(expected) if expected else None)


@pytest.mark.parametrize("time", ["24:00", "00:60", "4:30", "04:30:00", "bad"])
def test_invalid_time(time):
    assert next_fire(schedule(time=time), D(2026, 10, 4)) is None


def test_store(tmp_path):
    store = ScheduleStore(tmp_path)
    s = schedule(id="test")
    store.upsert(s)
    s.note = "中文備註"
    store.upsert(s)
    assert len(store.list()) == 1
    store.rename_chain("每日任務", "新任務")
    assert store.list()[0].chain == "新任務"
    assert "中文備註" in store.path.read_text(encoding="utf-8")
    store.mark_fired(s.id, D(2026, 10, 4))
    assert ScheduleStore(tmp_path).last_fired(s.id) == D(2026, 10, 4)
    store.delete(s.id)
    assert store.list() == []
    assert ScheduleStore(tmp_path).last_fired(s.id) is None
    assert not list(tmp_path.glob("*.tmp"))


def test_first_seen_persisted_and_no_history(tmp_path):
    s = schedule()
    now = D(2026, 10, 4, 5)
    store = ScheduleStore(tmp_path)
    assert not check_due([s], store, now).due
    store = ScheduleStore(tmp_path)
    assert not check_due([s], store, D(2026, 10, 4, 5, 1)).due
    result = check_due([s], store, D(2026, 10, 5, 4, 31))
    assert result.due == [(s, D(2026, 10, 5, 4, 30))]
    store.mark_fired(s.id, result.due[0][1])
    assert not check_due([s], store, D(2026, 10, 5, 4, 32)).due


@pytest.mark.parametrize("catch_up,minute,due", [(60, 30, True), (60, 90, False), (0, 2, True), (0, 3, False)])
def test_catch_up(tmp_path, catch_up, minute, due):
    from datetime import timedelta
    s = schedule(catch_up_minutes=catch_up)
    store = ScheduleStore(tmp_path)
    check_due([s], store, D(2026, 10, 4))
    t = D(2026, 10, 4, 4, 30)
    result = check_due([s], store, t + timedelta(minutes=minute))
    assert bool(result.due) == due
    assert bool(result.skipped) != due
    assert store.last_fired(s.id) == (None if due else t)


@pytest.mark.parametrize("kind,extra,latest", [
    ("daily", {}, D(2026, 10, 7, 4, 30)),
    ("weekly", {"weekdays": [0, 2]}, D(2026, 10, 7, 4, 30)),
    ("interval", {"interval_minutes": 1}, D(2026, 10, 7, 12)),
    ("once", {"date": "2026-10-05"}, D(2026, 10, 5, 4, 30)),
])
def test_skipped_advances(tmp_path, kind, extra, latest):
    s = schedule(kind=kind, **extra)
    store = ScheduleStore(tmp_path)
    check_due([s], store, D(2026, 10, 4))
    now = D(2026, 10, 7, 12)
    assert len(check_due([s], store, now).skipped) == 1
    assert store.last_fired(s.id) == latest
    result = check_due([s], store, now)
    assert not result.due and not result.skipped


def test_disabled_and_sorted(tmp_path):
    disabled = schedule(enabled=False)
    early, late = schedule(time="01:00"), schedule(time="02:00")
    schedules = [late, disabled, early]
    store = ScheduleStore(tmp_path)
    check_due(schedules, store, D(2026, 10, 4))
    assert [s for s, t in check_due(schedules, store, D(2026, 10, 4, 2)).due] == [early, late]
    assert upcoming(schedules, D(2026, 10, 4), 1) == [(D(2026, 10, 4, 1), early)]
    assert upcoming(schedules, D(2026, 10, 4), 0) == []
    assert next_fire(disabled, D(2026, 10, 4)) is None


@pytest.mark.parametrize("filename,content", [
    ("schedules.yaml", "[bad"), ("schedules.yaml", "- chain: x\n  kind: invalid"),
    ("schedules.yaml", "{}"), ("schedule_state.json", "broken"),
    ("schedule_state.json", '{"x":{"last_fired":"bad"}}'),
    ("schedule_state.json", "[]"),
])
def test_corrupt_files(tmp_path, filename, content, caplog):
    path = tmp_path / filename
    path.write_text(content, encoding="utf-8")
    store = ScheduleStore(tmp_path)
    assert store.list() == []
    assert store.last_fired("x") is None
    assert path.with_name(filename + ".bak").read_text(encoding="utf-8") == content
    assert "損毀" in caplog.text
    # 再次損毀也保留舊備份。
    path.write_text(content, encoding="utf-8")
    ScheduleStore(tmp_path).list()
    assert len(list(tmp_path.glob("*.bak"))) == 2
