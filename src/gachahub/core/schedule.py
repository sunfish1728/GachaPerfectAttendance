"""排程資料模型與介面（M3）。

排程存於 data/schedules.yaml；每個排程上次觸發時間存於 data/schedule_state.json。
時間一律使用本機時間（naive datetime）。
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field
import yaml

log = logging.getLogger("gachahub")


class ScheduleKind(str, Enum):
    DAILY = "daily"  # 每天 time
    WEEKLY = "weekly"  # weekdays 中的每一天 time（0=星期一 … 6=星期日）
    INTERVAL = "interval"  # 從 time（當天的錨點）起每 interval_minutes 分鐘
    ONCE = "once"  # date 當天 time，只觸發一次


class Schedule(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    chain: str  # 任務鏈名稱
    enabled: bool = True
    kind: ScheduleKind = ScheduleKind.DAILY
    time: str = "04:30"  # "HH:MM"
    weekdays: list[int] = Field(default_factory=lambda: list(range(7)))
    date: str | None = None  # "YYYY-MM-DD"，ONCE 使用
    interval_minutes: int = 240
    # 錯過補跑：程式未開啟或電腦睡眠而錯過觸發時，在錯過後這段時間內仍會補跑一次；0 = 不補跑
    catch_up_minutes: int = 60
    # 透過 Windows 工作排程器在觸發前喚醒電腦並啟動本程式
    wake_computer: bool = False
    note: str = ""


class ScheduleStore:
    """schedules.yaml 讀寫（原子寫入）與 schedule_state.json（各排程上次觸發時間）。"""

    def __init__(self, data_dir: Path):
        self.dir = Path(data_dir)
        self.path = self.dir / "schedules.yaml"
        self.state_path = self.dir / "schedule_state.json"
        self._state = self._load(self.state_path, self._parse_state, {})

    @staticmethod
    def _parse_state(text):
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("排程狀態必須是物件")
        for key, item in data.items():
            if not isinstance(key, str) or not isinstance(item, dict):
                raise ValueError("排程狀態格式錯誤")
            for field, value in item.items():
                if field not in ("last_fired", "first_seen"):
                    raise ValueError("未知的排程狀態欄位")
                if datetime.fromisoformat(value).tzinfo is not None:
                    raise ValueError("排程時間必須是本機時間")
        return data

    def _load(self, path, parse, empty):
        if not path.exists():
            return empty
        try:
            return parse(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError, yaml.YAMLError) as exc:
            log.warning("排程檔案損毀，視為空：%s（%s）", path, exc)
            backup = path.with_name(path.name + ".bak")
            while backup.exists():
                backup = path.with_name(path.name + f".{uuid.uuid4().hex}.bak")
            try:
                path.replace(backup)
            except OSError:
                log.exception("無法保留損毀的排程檔案：%s", path)
            return empty

    def _write(self, path, text):
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        try:
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(path)
        finally:
            tmp.unlink(missing_ok=True)

    def _save_state(self):
        self._write(self.state_path, json.dumps(self._state, ensure_ascii=False, indent=2))

    def list(self) -> list[Schedule]:
        def parse(text):
            data = yaml.safe_load(text)
            if data is None:
                return []
            if not isinstance(data, list):
                raise ValueError("排程設定必須是清單")
            return [Schedule.model_validate(item) for item in data]
        return self._load(self.path, parse, [])

    def save_all(self, schedules: list[Schedule]) -> None:
        self._write(self.path, yaml.safe_dump(
            [s.model_dump(mode="json") for s in schedules], allow_unicode=True, sort_keys=False))

    def upsert(self, schedule: Schedule) -> None:
        schedules = self.list()
        for index, item in enumerate(schedules):
            if item.id == schedule.id:
                schedules[index] = schedule
                break
        else:
            schedules.append(schedule)
        self.save_all(schedules)

    def delete(self, schedule_id: str) -> None:
        self.save_all([s for s in self.list() if s.id != schedule_id])
        self._state.pop(schedule_id, None)
        self._save_state()

    def rename_chain(self, old: str, new: str) -> None:
        schedules = self.list()
        for s in schedules:
            if s.chain == old:
                s.chain = new
        self.save_all(schedules)

    def last_fired(self, schedule_id: str) -> datetime | None:
        value = self._state.get(schedule_id, {}).get("last_fired")
        return datetime.fromisoformat(value) if value else None

    def mark_fired(self, schedule_id: str, when: datetime) -> None:
        self._state.setdefault(schedule_id, {})["last_fired"] = when.isoformat()
        self._save_state()

    def _first_seen(self, schedule_id: str, now: datetime) -> datetime:
        state = self._state.setdefault(schedule_id, {})
        if "first_seen" not in state:
            state["first_seen"] = now.isoformat()
            self._save_state()
        return datetime.fromisoformat(state["first_seen"])


def _anchor(s: Schedule, day: datetime) -> datetime:
    if not re.fullmatch(r"\d{2}:\d{2}", s.time):
        raise ValueError("時間必須是 HH:MM")
    hour, minute = map(int, s.time.split(":"))
    return day.replace(hour=hour, minute=minute, second=0, microsecond=0)


def next_fire(s: Schedule, after: datetime) -> datetime | None:
    """嚴格晚於 after 的下一次觸發時間；停用、ONCE 已過、設定無效時回傳 None。"""
    if not s.enabled:
        return None
    try:
        anchor = _anchor(s, after)
        if s.kind == ScheduleKind.ONCE:
            if not s.date or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", s.date):
                return None
            target = _anchor(s, datetime.fromisoformat(s.date))
            return target if target > after else None
        if s.kind == ScheduleKind.INTERVAL:
            if s.interval_minutes < 1:
                return None
            step = timedelta(minutes=s.interval_minutes)
            return anchor + ((after - anchor) // step + 1) * step
        if s.kind == ScheduleKind.DAILY:
            return anchor if anchor > after else anchor + timedelta(days=1)
        if s.kind == ScheduleKind.WEEKLY:
            for offset in range(8):
                target = anchor + timedelta(days=offset)
                if target > after and target.weekday() in s.weekdays:
                    return target
    except (ValueError, OverflowError):
        return None
    return None


class DueResult(BaseModel):
    due: list[tuple[Schedule, datetime]] = Field(default_factory=list)  # 應觸發：(排程, 預定時間)，依時間排序
    skipped: list[tuple[Schedule, datetime]] = Field(default_factory=list)  # 錯過且超過補跑時間而略過的


def check_due(schedules: list[Schedule], store: ScheduleStore, now: datetime) -> DueResult:
    """檢查到期排程。

    規則：基準 = last_fired；若從未觸發，基準 = store 首次見到此排程的時間（由 store 記錄，
    避免新建立的排程立刻補跑過去的時間）。取基準之後第一個預定時間 t：
    - t <= now 且 now - t <= catch_up_minutes（catch_up=0 時容許 2 分鐘誤差）→ 列入 due（不自動 mark_fired，
      由呼叫端真正開始執行或使用者取消時呼叫 mark_fired(id, t)）。
    - t <= now 但超過補跑時間 → 列入 skipped，並由本函式 mark_fired 到最近一個 <= now 的預定時間。
    同一排程一次最多一筆。停用的排程不列入。
    """
    result = DueResult()
    for s in schedules:
        first_seen = store._first_seen(s.id, now)
        if not s.enabled:
            continue
        base = store.last_fired(s.id) or first_seen
        target = next_fire(s, base)
        if target is None or target > now:
            continue
        window = timedelta(minutes=max(0, s.catch_up_minutes) or 2)
        if now - target <= window:
            result.due.append((s, target))
        else:
            result.skipped.append((s, target))
            # 直接找最近一次，避免多年未開啟時逐分鐘追趕。
            latest = target
            if s.kind == ScheduleKind.INTERVAL:
                step = timedelta(minutes=s.interval_minutes)
                latest += ((now - latest) // step) * step
            elif s.kind in (ScheduleKind.DAILY, ScheduleKind.WEEKLY):
                anchor = _anchor(s, now)
                for offset in range(8):
                    candidate = anchor - timedelta(days=offset)
                    if candidate <= now and (s.kind == ScheduleKind.DAILY or candidate.weekday() in s.weekdays):
                        latest = candidate
                        break
            store.mark_fired(s.id, latest)
    result.due.sort(key=lambda item: item[1])
    result.skipped.sort(key=lambda item: item[1])
    return result


def upcoming(schedules: list[Schedule], now: datetime, limit: int = 10) -> list[tuple[datetime, Schedule]]:
    """接下來的觸發時間（供 GUI 顯示），依時間排序。"""
    times = [(target, s) for s in schedules if (target := next_fire(s, now)) is not None]
    return sorted(times, key=lambda item: item[0])[:max(0, limit)]
