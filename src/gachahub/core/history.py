"""執行歷史。每次操作開關連線，以 SQLite WAL 保存完整任務鏈結果。"""

from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from .models import ChainReport, StepStatus

log = logging.getLogger("gachahub")
_locks: dict[Path, threading.RLock] = {}
_locks_guard = threading.Lock()
_SCHEMA = """
CREATE TABLE runs (
    id INTEGER PRIMARY KEY, chain TEXT NOT NULL, trigger TEXT NOT NULL,
    schedule_id TEXT, started_at TEXT NOT NULL, finished_at TEXT,
    ok INTEGER NOT NULL, aborted INTEGER NOT NULL, cancelled INTEGER NOT NULL,
    total_steps INTEGER NOT NULL, failed_steps INTEGER NOT NULL,
    duration_sec REAL NOT NULL, summary TEXT NOT NULL
);
CREATE TABLE steps (
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    idx INTEGER NOT NULL, step TEXT NOT NULL, status TEXT NOT NULL,
    message TEXT NOT NULL, attempts INTEGER NOT NULL, started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL, duration_sec REAL NOT NULL,
    PRIMARY KEY (run_id, idx)
);
CREATE INDEX runs_started ON runs(started_at, id);
CREATE INDEX runs_chain_started ON runs(chain, started_at);
PRAGMA user_version = 1;
"""


class RunRecord(BaseModel):
    id: int
    chain: str
    trigger: str
    schedule_id: str | None
    started_at: datetime
    finished_at: datetime | None
    ok: bool
    aborted: bool
    cancelled: bool
    total_steps: int
    failed_steps: int
    duration_sec: float
    summary: str


class StepRecord(BaseModel):
    run_id: int
    idx: int  # 從 0 起，與 report.results 的順序相同
    step: str
    status: str
    message: str
    attempts: int
    started_at: datetime
    finished_at: datetime
    duration_sec: float


class DayStatus(BaseModel):
    date: date
    chain: str
    runs: int
    ok_runs: int
    last_status: Literal["success", "failed", "cancelled"]


def _local(value: datetime) -> datetime:
    """有時區的輸入先換成本機時間，資料庫統一用 naive datetime。"""
    return value.astimezone().replace(tzinfo=None) if value.tzinfo else value


class HistoryStore:
    def __init__(self, db_path: Path):
        self.path = Path(db_path)
        with _locks_guard:
            self._lock = _locks.setdefault(self.path.resolve(), threading.RLock())
        self._memory: sqlite3.Connection | None = None
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                conn = self._connect(wal=False)
                try:
                    self._validate(conn)
                    conn.execute("PRAGMA journal_mode = WAL")
                finally:
                    conn.close()
            except (sqlite3.DatabaseError, OSError) as exc:
                if self._busy(exc):
                    log.warning("歷史資料庫暫時被鎖定，改用記憶體；原檔保持原樣")
                    self._fallback()
                else:
                    self._recover()

    @staticmethod
    def _busy(exc):
        return isinstance(exc, sqlite3.DatabaseError) and (
            getattr(exc, "sqlite_errorcode", None) in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)
            or "locked" in str(exc).lower() or "busy" in str(exc).lower()
        )

    def _fallback(self):
        self._memory = sqlite3.connect(":memory:", check_same_thread=False)
        self._memory.row_factory = sqlite3.Row
        self._memory.execute("PRAGMA foreign_keys = ON")
        self._memory.executescript(_SCHEMA)

    def _connect(self, *, wal=True):
        conn = sqlite3.connect(self.path, timeout=10)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            if wal:
                conn.execute("PRAGMA journal_mode = WAL")
            return conn
        except Exception:
            conn.close()
            raise

    def _validate(self, conn):
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if version == 0 and not tables:
            conn.executescript(_SCHEMA)
        elif version != 1:
            raise sqlite3.DatabaseError("不支援的歷史資料版本")
        integer_fields = {"id", "run_id", "idx", "ok", "aborted", "cancelled", "total_steps",
                          "failed_steps", "attempts"}
        for table, model in (("runs", RunRecord), ("steps", StepRecord)):
            columns = list(conn.execute(f"PRAGMA table_info({table})"))
            if {r[1] for r in columns} != set(model.model_fields):
                raise sqlite3.DatabaseError("歷史資料表欄位不符")
            for column in columns:
                name = column[1]
                kind = "INTEGER" if name in integer_fields else "REAL" if name == "duration_sec" else "TEXT"
                pk = int(name == "id") if table == "runs" else {"run_id": 1, "idx": 2}.get(name, 0)
                required = int(name not in ("id", "schedule_id", "finished_at")) if table == "runs" else 1
                if (column[2].upper(), column[3], column[5]) != (kind, required, pk):
                    raise sqlite3.DatabaseError("歷史資料表型別或鍵值不符")
        if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise sqlite3.DatabaseError("歷史資料完整性檢查失敗")
        if list(conn.execute("PRAGMA foreign_key_check")):
            raise sqlite3.DatabaseError("歷史步驟關聯不符")
        relations = list(conn.execute("PRAGMA foreign_key_list(steps)"))
        if len(relations) != 1 or tuple(relations[0])[2:7] != ("runs", "run_id", "id", "NO ACTION", "CASCADE"):
            raise sqlite3.DatabaseError("歷史步驟關聯不符")

    def _recover(self):
        log.warning("歷史資料庫無法使用，保留備份並重建：%s", self.path)
        try:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            backup = self.path.with_name(self.path.name + f".bak-{stamp}")
            # WAL 也保留，避免遺失尚未寫回主檔的資料。
            for suffix in ("", "-wal", "-shm"):
                source = Path(str(self.path) + suffix)
                if source.exists():
                    source.replace(Path(str(backup) + suffix))
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = self._connect()
            try:
                conn.executescript(_SCHEMA)
            finally:
                conn.close()
        except (sqlite3.DatabaseError, OSError):
            log.exception("無法重建歷史資料庫，暫存於記憶體（關閉程式後會遺失）")
            self._fallback()

    def _use(self, operation):
        with self._lock:
            for attempt in range(2):
                conn = None
                try:
                    conn = self._memory if self._memory is not None else self._connect()
                    with conn:
                        return operation(conn)
                except sqlite3.DatabaseError as exc:
                    # 鎖定不是損毀，不可搬走正在使用的資料庫。
                    if self._busy(exc):
                        raise
                    if attempt:
                        raise
                finally:
                    if conn is not None and conn is not self._memory:
                        conn.close()
                self._recover()

    def record(self, report: ChainReport, trigger="manual", schedule_id=None) -> int:
        start = _local(report.started_at)
        end = _local(report.finished_at or datetime.now(tz=report.started_at.tzinfo))
        failed = [r.step for r in report.results if r.status in (StepStatus.FAILED, StepStatus.TIMEOUT)]
        successes = sum(r.status == StepStatus.SUCCESS for r in report.results)
        summary = f"{successes}/{len(report.results)} 成功"
        if failed:
            summary += "，失敗：" + "、".join(failed)
        if report.cancelled:
            summary = "已停止"
        elif report.aborted:
            summary += "，已中止"

        def write(conn):
            cursor = conn.execute(
                "INSERT INTO runs (chain, trigger, schedule_id, started_at, finished_at, ok, aborted, "
                "cancelled, total_steps, failed_steps, duration_sec, summary) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (report.chain, trigger, schedule_id, start.isoformat(), end.isoformat(), report.ok,
                 report.aborted, report.cancelled, len(report.results), len(failed),
                 max(0, (end - start).total_seconds()), summary),
            )
            run_id = cursor.lastrowid
            conn.executemany("INSERT INTO steps VALUES (?,?,?,?,?,?,?,?,?)", [
                (run_id, idx, r.step, r.status.value, r.message, r.attempts,
                 _local(r.started_at).isoformat(), _local(r.finished_at).isoformat(),
                 max(0, (r.finished_at - r.started_at).total_seconds()))
                for idx, r in enumerate(report.results)
            ])
            return run_id
        return self._use(write)

    @staticmethod
    def _filter(chain=None, since=None):
        clauses, values = [], []
        if chain is not None:
            clauses.append("chain = ?")
            values.append(chain)
        if since is not None:
            clauses.append("started_at >= ?")
            values.append(_local(since).isoformat())
        return (" WHERE " + " AND ".join(clauses) if clauses else ""), values

    def runs(self, limit=50, offset=0, chain: str | None = None,
             since: datetime | None = None) -> list[RunRecord]:
        where, values = self._filter(chain, since)
        return self._use(lambda c: [RunRecord.model_validate(dict(r)) for r in c.execute(
            "SELECT * FROM runs" + where + " ORDER BY started_at DESC, id DESC LIMIT ? OFFSET ?",
            [*values, max(0, limit), max(0, offset)],
        )])

    def count(self, chain=None, since=None) -> int:
        where, values = self._filter(chain, since)
        return self._use(lambda c: c.execute("SELECT COUNT(*) FROM runs" + where, values).fetchone()[0])

    def steps(self, run_id) -> list[StepRecord]:
        return self._use(lambda c: [StepRecord.model_validate(dict(r)) for r in c.execute(
            "SELECT * FROM steps WHERE run_id = ? ORDER BY idx", (run_id,),
        )])

    def daily(self, days=7, today: date | None = None) -> list[DayStatus]:
        if days <= 0:
            return []
        today = today or date.today()
        start = today - timedelta(days=days - 1)
        end = today + timedelta(days=1)

        def read(conn):
            groups = {}
            for r in conn.execute("SELECT * FROM runs WHERE started_at >= ? AND started_at < ? "
                                  "ORDER BY started_at, id", (start.isoformat(), end.isoformat())):
                day = datetime.fromisoformat(r["started_at"]).date()
                key = (day, r["chain"])
                item = groups.setdefault(key, DayStatus(date=day, chain=r["chain"], runs=0,
                                                       ok_runs=0, last_status="success"))
                item.runs += 1
                item.ok_runs += bool(r["ok"])
                item.last_status = "cancelled" if r["cancelled"] else "success" if r["ok"] else "failed"
            return [groups[key] for key in sorted(groups, key=lambda k: (-k[0].toordinal(), k[1]))]
        return self._use(read)

    def chains(self) -> list[str]:
        return self._use(lambda c: [r[0] for r in c.execute("SELECT DISTINCT chain FROM runs ORDER BY chain")])

    def stats(self, since: datetime) -> dict:
        def read(conn):
            row = conn.execute(
                "SELECT COUNT(*) AS runs, COALESCE(SUM(ok),0) AS ok_runs, "
                "COALESCE(SUM(NOT ok AND NOT cancelled),0) AS failed_runs, "
                "COALESCE(SUM(cancelled),0) AS cancelled_runs, "
                "COALESCE(SUM(duration_sec),0.0) AS total_duration_sec FROM runs WHERE started_at >= ?",
                (_local(since).isoformat(),),
            ).fetchone()
            return dict(row)
        return self._use(read)

    def prune(self, keep_days=180) -> int:
        cutoff = datetime.now() - timedelta(days=max(0, keep_days))
        return self._use(lambda c: c.execute("DELETE FROM runs WHERE started_at < ?",
                                             (cutoff.isoformat(),)).rowcount)

    def delete_run(self, run_id):
        self._use(lambda c: c.execute("DELETE FROM runs WHERE id = ?", (run_id,)).rowcount)
