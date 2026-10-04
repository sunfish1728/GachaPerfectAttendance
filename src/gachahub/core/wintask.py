"""Windows 喚醒工作；只啟動程式，實際執行時間交給本機排程器。"""

from __future__ import annotations

import csv
import io
import logging
import re
import subprocess
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from xml.etree import ElementTree as ET

from .config import app_root
from .process import is_admin
from .schedule import Schedule, ScheduleKind, _anchor

TASK_FOLDER = "\\GachaHub\\"
_NS = "http://schemas.microsoft.com/windows/2004/02/mit/task"
_DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
log = logging.getLogger("gachahub")


def task_name(schedule_id: str) -> str:
    # 保留正常 ID，不允許路徑分隔符讓工作跑到別的資料夾。
    if not schedule_id or re.search(r'[\\/:*?"<>|\x00-\x1f]', schedule_id):
        raise ValueError("排程 ID 含有無效的工作名稱字元")
    return TASK_FOLDER + schedule_id


def build_xml(schedule, python_exe: str, workdir: str, lead_minutes: int = 3, highest: bool = True) -> str:
    """產生 UTF-16 工作 XML；PT0S 避免常駐程式被工作逾時終止。

    不限制執行時間的取捨是工作會維持「執行中」，IgnoreNew 會略過重複啟動；
    程式本身仍持續檢查排程，退出後下一個觸發器可再次啟動。
    """
    task_name(schedule.id)
    if lead_minutes < 0:
        raise ValueError("提前喚醒分鐘數不可小於 0")
    day = datetime.now()
    if schedule.kind == ScheduleKind.ONCE:
        if not schedule.date or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", schedule.date):
            raise ValueError("單次排程日期必須是 YYYY-MM-DD")
        day = datetime.fromisoformat(schedule.date)
    anchor = _anchor(schedule, day)
    boundary = anchor - timedelta(minutes=lead_minutes)
    shift = (boundary.date() - anchor.date()).days
    if schedule.kind == ScheduleKind.INTERVAL and schedule.interval_minutes < 1:
        raise ValueError("排程間隔至少為 1 分鐘")
    if schedule.kind == ScheduleKind.WEEKLY and (
        not schedule.weekdays or any(d not in range(7) for d in schedule.weekdays)
    ):
        raise ValueError("每週排程必須選擇有效的星期")

    def add(parent, tag, value=None, **attrs):
        node = ET.SubElement(parent, tag, attrs)
        if value is not None:
            node.text = str(value)
        return node

    root = ET.Element("Task", {"version": "1.2", "xmlns": _NS})
    info = add(root, "RegistrationInfo")
    add(info, "Description", f"由二游腳本集合站建立：{schedule.chain}；提前喚醒並啟動常駐程式。")
    triggers = add(root, "Triggers")
    calendar = schedule.kind in (ScheduleKind.DAILY, ScheduleKind.WEEKLY)
    trigger = add(triggers, "CalendarTrigger" if calendar else "TimeTrigger")
    if schedule.kind == ScheduleKind.INTERVAL:
        repetition = add(trigger, "Repetition")
        add(repetition, "Interval", f"PT{schedule.interval_minutes}M")
        add(repetition, "StopAtDurationEnd", "false")  # 不設定 Duration，即無限期
    add(trigger, "StartBoundary", boundary.isoformat(timespec="seconds"))
    add(trigger, "Enabled", str(schedule.enabled).lower())
    if schedule.kind == ScheduleKind.DAILY:
        add(add(trigger, "ScheduleByDay"), "DaysInterval", "1")
    elif schedule.kind == ScheduleKind.WEEKLY:
        weekly = add(trigger, "ScheduleByWeek")
        add(weekly, "WeeksInterval", "1")
        days = add(weekly, "DaysOfWeek")
        for d in sorted({(d + shift) % 7 for d in schedule.weekdays}):
            add(days, _DAYS[d])
    principals = add(root, "Principals")
    principal = add(principals, "Principal", id="Author")
    add(principal, "LogonType", "InteractiveToken")
    add(principal, "RunLevel", "HighestAvailable" if highest else "LeastPrivilege")
    settings = add(root, "Settings")
    for key, value in (
        ("MultipleInstancesPolicy", "IgnoreNew"),
        ("DisallowStartIfOnBatteries", "false"),
        ("StopIfGoingOnBatteries", "false"),
        ("StartWhenAvailable", "true"),
        ("WakeToRun", "true"),
        ("ExecutionTimeLimit", "PT0S"),
    ):
        add(settings, key, value)
    action = add(add(root, "Actions", Context="Author"), "Exec")
    # 直接啟動 pythonw，不透過 shell；空白、& 等路徑字元由 XML 正確跳脫。
    exe = Path(python_exe)
    if exe.name.lower() == "python.exe":
        exe = exe.with_name("pythonw.exe")
    add(action, "Command", str(exe))
    add(action, "Arguments", "-m gachahub --minimized --from-task")
    add(action, "WorkingDirectory", workdir)
    return '<?xml version="1.0" encoding="UTF-16"?>\n' + ET.tostring(root, encoding="unicode")


def _decode(value: bytes | str | None) -> str:
    if isinstance(value, str):
        return value
    for encoding in ("utf-8-sig", "mbcs", "cp950", "cp437"):
        try:
            return (value or b"").decode(encoding)
        except (UnicodeError, LookupError):
            continue
    return (value or b"").decode("utf-8", errors="replace")


def _run(args):
    return subprocess.run(
        ["schtasks", *args], capture_output=True, timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def register(schedule, python_exe, workdir, lead_minutes=3, highest=True) -> tuple[bool, str]:
    if highest and not is_admin():
        return False, "建立最高權限喚醒工作需要管理員權限，請以管理員身分重新啟動二游腳本集合站。"
    path = None
    try:
        xml = build_xml(schedule, python_exe, workdir, lead_minutes, highest)
        runtime = app_root() / "runtime"
        runtime.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=runtime, suffix=".xml", delete=False) as file:
            path = Path(file.name)
            file.write(xml.encode("utf-16"))
        result = _run(["/Create", "/TN", task_name(schedule.id), "/XML", str(path), "/F"])
        if result.returncode == 0:
            return True, f"已建立／更新喚醒工作：{schedule.id}"
        return False, f"建立喚醒工作失敗：{schedule.id}；{_decode(result.stderr or result.stdout).strip()}"
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return False, f"建立喚醒工作失敗：{schedule.id}；{exc}"
    finally:
        if path is not None:
            path.unlink(missing_ok=True)


def _query() -> list[str]:
    result = _run(["/Query", "/FO", "CSV", "/NH"])
    if result.returncode:
        raise OSError(_decode(result.stderr or result.stdout).strip())
    names = []
    for row in csv.reader(io.StringIO(_decode(result.stdout))):
        if row and row[0].casefold().startswith(TASK_FOLDER.casefold()):
            names.append(row[0])
    return sorted(set(names))


def unregister(schedule_id) -> tuple[bool, str]:
    try:
        name = task_name(schedule_id)
        result = _run(["/Delete", "/TN", name, "/F"])
        if result.returncode == 0:
            return True, f"已刪除喚醒工作：{schedule_id}"
        # 以查詢確認不存在，避免依賴 Windows 的語系訊息或把拒絕存取當成功。
        if name.casefold() not in {n.casefold() for n in _query()}:
            return True, f"喚醒工作不存在：{schedule_id}"
        return False, f"刪除喚醒工作失敗：{schedule_id}；{_decode(result.stderr or result.stdout).strip()}"
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return False, f"刪除喚醒工作失敗：{schedule_id}；{exc}"


def list_registered() -> list[str]:
    """回傳包含資料夾前綴的工作名稱；查詢失敗記錄日誌。"""
    try:
        return _query()
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("無法查詢喚醒工作：%s", exc)
        return []


def sync(schedules, python_exe, workdir, lead_minutes=3, highest=True) -> list[str]:
    """同步啟用且要求喚醒的排程；回傳動作成功或失敗的繁中訊息。"""
    try:
        registered = _query()
        wanted = {task_name(s.id): s for s in schedules if s.enabled and s.wake_computer}
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return [f"同步喚醒工作失敗：{exc}"]
    messages = []
    for s in wanted.values():
        _, message = register(s, python_exe, workdir, lead_minutes, highest)
        messages.append(message)
    for name in registered:
        if name.casefold() not in {n.casefold() for n in wanted}:
            _, message = unregister(name[len(TASK_FOLDER):])
            messages.append(message)
    return messages
