from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
import sqlite3

import pytest

from gachahub.core.history import HistoryStore
from gachahub.core.models import ChainReport, StepResult, StepStatus


def report(at=None, chain="每日清體力", status=StepStatus.SUCCESS, **kwargs):
    at = at or datetime.now()
    return ChainReport(chain=chain, started_at=at, finished_at=at + timedelta(seconds=10),
                       results=[StepResult(step="絕區零一條龍", status=status, message="測試訊息",
                                           attempts=2, started_at=at,
                                           finished_at=at + timedelta(seconds=5))], **kwargs)


def test_record_read_and_delete(tmp_path):
    path = tmp_path / "data" / "history.db"
    store = HistoryStore(path)
    rep = report(status=StepStatus.FAILED, aborted=True)
    run_id = store.record(rep, trigger="schedule", schedule_id="s1")
    row = store.runs()[0]
    assert row.id == run_id
    assert row.chain == rep.chain
    assert row.started_at == rep.started_at
    assert row.finished_at == rep.finished_at
    assert (row.trigger, row.schedule_id) == ("schedule", "s1")
    assert (row.ok, row.aborted, row.cancelled) == (False, True, False)
    assert (row.total_steps, row.failed_steps, row.duration_sec) == (1, 1, 10)
    assert "0/1 成功，失敗：絕區零一條龍" in row.summary
    step = store.steps(run_id)[0]
    assert (step.run_id, step.idx, step.status, step.attempts) == (run_id, 0, "failed", 2)
    assert step.duration_sec == 5
    assert step.message == "測試訊息"
    assert HistoryStore(path).runs() == store.runs()
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    store.delete_run(run_id)
    assert store.count() == 0 and store.steps(run_id) == []
    store.delete_run(run_id)


def test_missing_finish_and_summary(tmp_path):
    store = HistoryStore(tmp_path / "history.db")
    rep = report(at=datetime.now() - timedelta(seconds=10))
    rep.finished_at = None
    store.record(rep)
    row = store.runs()[0]
    assert row.finished_at is not None and row.duration_sec >= 10
    assert row.summary == "1/1 成功"
    store.record(report(cancelled=True, status=StepStatus.CANCELLED))
    assert store.runs()[0].summary == "已停止"


def test_pagination_filter_and_chains(tmp_path):
    store = HistoryStore(tmp_path / "history.db")
    start = datetime(2026, 10, 1)
    ids = [store.record(report(start + timedelta(hours=i), chain="A" if i % 2 else "B"))
           for i in range(6)]
    assert [r.id for r in store.runs(limit=2, offset=2)] == list(reversed(ids))[2:4]
    assert store.runs(limit=0) == []
    assert store.runs(offset=100) == []
    assert store.count(chain="A") == 3
    assert store.count(since=start + timedelta(hours=4)) == 2
    assert store.count(chain="A", since=start + timedelta(hours=4)) == 1
    assert [r.id for r in store.runs(chain="A", since=start + timedelta(hours=4))] == [ids[5]]
    assert store.chains() == ["A", "B"]
    # 同一時間以 id 排序，最後寫入的排在前面。
    last = store.record(report(start + timedelta(hours=5), chain="A"))
    assert store.runs()[0].id == last


def test_daily_last_status_and_window(tmp_path):
    store = HistoryStore(tmp_path / "history.db")
    store.record(report(datetime(2026, 10, 1, 23, 59), chain="A"))
    store.record(report(datetime(2026, 10, 2, 0), chain="A"))
    store.record(report(datetime(2026, 10, 2, 12), chain="A", status=StepStatus.TIMEOUT))
    store.record(report(datetime(2026, 10, 2, 12), chain="A", cancelled=True))
    store.record(report(datetime(2026, 10, 2, 13), chain="B"))
    store.record(report(datetime(2026, 10, 3, 23, 59), chain="A", status=StepStatus.FAILED))
    store.record(report(datetime(2026, 10, 4, 0), chain="A"))
    rows = store.daily(days=2, today=date(2026, 10, 3))
    assert [(r.date, r.chain, r.runs, r.ok_runs, r.last_status) for r in rows] == [
        (date(2026, 10, 3), "A", 1, 0, "failed"),
        (date(2026, 10, 2), "A", 3, 1, "cancelled"),
        (date(2026, 10, 2), "B", 1, 1, "success"),
    ]
    assert store.daily(days=0) == []
    assert store.daily(days=1, today=date(2026, 10, 5)) == []


def test_stats_and_prune(tmp_path):
    store = HistoryStore(tmp_path / "history.db")
    now = datetime.now()
    old_id = store.record(report(now - timedelta(days=181)))
    store.record(report(now - timedelta(days=1)))
    store.record(report(now, status=StepStatus.FAILED))
    store.record(report(now, cancelled=True))
    assert store.stats(now - timedelta(days=2)) == {
        "runs": 3, "ok_runs": 1, "failed_runs": 1, "cancelled_runs": 1, "total_duration_sec": 30.0,
    }
    assert store.stats(now + timedelta(days=2))["runs"] == 0
    assert store.prune() == 1
    assert store.steps(old_id) == []
    assert store.count() == 3
    assert store.prune() == 0


def test_threads_and_multiple_stores(tmp_path):
    path = tmp_path / "history.db"
    stores = [HistoryStore(path), HistoryStore(path)]

    def write(i):
        store = stores[i % 2]
        run_id = store.record(report(chain=str(i)))
        assert len(store.steps(run_id)) == 1
        store.count()
        return run_id

    with ThreadPoolExecutor(max_workers=8) as executor:
        ids = list(executor.map(write, range(80)))
    assert len(set(ids)) == 80
    assert stores[0].count() == 80


@pytest.mark.parametrize("kind", ["bytes", "version", "columns", "relation", "types"])
def test_corrupt_database_backup(tmp_path, kind, caplog):
    path = tmp_path / "history.db"
    if kind == "bytes":
        path.write_bytes(b"broken sqlite database")
    else:
        with closing(sqlite3.connect(path)) as conn:
            if kind == "version":
                conn.execute("PRAGMA user_version = 99")
            elif kind == "columns":
                conn.execute("CREATE TABLE runs (id INTEGER)")
                conn.execute("PRAGMA user_version = 1")
            elif kind == "relation":
                # 正確欄位但缺少外鍵，也必須辨識。
                from gachahub.core.history import _SCHEMA
                conn.executescript(_SCHEMA.replace("REFERENCES runs(id) ON DELETE CASCADE", ""))
            else:
                from gachahub.core.history import _SCHEMA
                conn.executescript(_SCHEMA.replace("id INTEGER PRIMARY KEY", "id TEXT PRIMARY KEY"))
    original = path.read_bytes()
    store = HistoryStore(path)
    backups = list(tmp_path.glob("history.db.bak-*"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == original
    assert store.count() == 0
    assert "重建" in caplog.text
    store.record(report())
    assert store.count() == 1


def test_damage_after_open(tmp_path):
    path = tmp_path / "history.db"
    store = HistoryStore(path)
    path.write_bytes(b"damaged after init")
    assert store.count() == 0
    assert list(tmp_path.glob("history.db.bak-*"))
    store.record(report())
    assert store.count() == 1


def test_cannot_open_uses_memory(tmp_path, monkeypatch, caplog):
    path = tmp_path / "history.db"
    real_connect = sqlite3.connect

    def blocked(name, **kwargs):
        if name != ":memory:":
            raise sqlite3.OperationalError("unable to open database file")
        return real_connect(name, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", blocked)
    store = HistoryStore(path)
    store.record(report())
    assert store.count() == 1
    assert "記憶體" in caplog.text


def test_timezone_normalized_to_local(tmp_path):
    store = HistoryStore(tmp_path / "history.db")
    at = datetime(2026, 10, 3, 20, tzinfo=timezone.utc)
    store.record(report(at))
    assert store.runs()[0].started_at == at.astimezone().replace(tzinfo=None)
    assert store.count(since=at) == 1


def test_locked_database_never_backed_up(tmp_path, monkeypatch):
    path = tmp_path / "history.db"
    store = HistoryStore(path)
    store.record(report())
    original = path.read_bytes()
    real_connect = sqlite3.connect

    def locked(name, **kwargs):
        if name != ":memory:":
            raise sqlite3.OperationalError("database is locked")
        return real_connect(name, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", locked)
    temporary = HistoryStore(path)
    temporary.record(report())
    assert temporary.count() == 1
    assert path.read_bytes() == original
    assert not list(tmp_path.glob("history.db.bak-*"))
    with pytest.raises(sqlite3.OperationalError, match="locked"):
        store.count()


def test_multiple_steps_and_empty_report(tmp_path):
    store = HistoryStore(tmp_path / "history.db")
    rep = report()
    rep.results.append(rep.results[0].model_copy(update={"step": "第二步", "status": StepStatus.SKIPPED}))
    run_id = store.record(rep)
    assert [(s.idx, s.step, s.status) for s in store.steps(run_id)] == [
        (0, "絕區零一條龍", "success"), (1, "第二步", "skipped")]
    assert store.runs()[0].ok and store.runs()[0].failed_steps == 0
    rep.results = []
    rep.aborted = True
    empty = store.record(rep)
    assert store.steps(empty) == []
    assert not store.runs()[0].ok
