import subprocess
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from gachahub.core import wintask as w
from gachahub.core.schedule import Schedule

NS = {"t": w._NS}


def schedule(**kwargs):
    return Schedule(id="test", chain="中文 & 任務", **kwargs)


def xml(s, **kwargs):
    text = w.build_xml(s, r"C:\程式 & 空白\python.exe", r"C:\專案 空白", **kwargs)
    assert 'encoding="UTF-16"' in text
    return ET.fromstring(text)


@pytest.mark.parametrize("kind", ["daily", "weekly", "interval", "once"])
def test_xml(kind):
    s = schedule(kind=kind, date="2026-10-05", weekdays=[0, 6], interval_minutes=17)
    root = xml(s)
    def value(path):
        return root.find(path, NS).text
    assert value("t:Principals/t:Principal/t:RunLevel") == "HighestAvailable"
    assert value("t:Principals/t:Principal/t:LogonType") == "InteractiveToken"
    for key, expected in {
        "WakeToRun": "true", "StartWhenAvailable": "true",
        "DisallowStartIfOnBatteries": "false", "StopIfGoingOnBatteries": "false",
        "ExecutionTimeLimit": "PT0S", "MultipleInstancesPolicy": "IgnoreNew",
    }.items():
        assert value(f"t:Settings/t:{key}") == expected
    assert "二游腳本集合站" in value("t:RegistrationInfo/t:Description")
    assert value("t:Actions/t:Exec/t:Command").endswith("pythonw.exe")
    assert value("t:Actions/t:Exec/t:Arguments") == "-m gachahub --minimized --from-task"
    assert value("t:Actions/t:Exec/t:WorkingDirectory") == r"C:\專案 空白"
    trigger = root.find("t:Triggers", NS)[0]
    assert trigger.tag.endswith("CalendarTrigger" if kind in ("daily", "weekly") else "TimeTrigger")
    if kind == "daily":
        assert trigger.find("t:ScheduleByDay/t:DaysInterval", NS).text == "1"
    elif kind == "weekly":
        assert {x.tag.split("}")[1] for x in trigger.find("t:ScheduleByWeek/t:DaysOfWeek", NS)} == {"Monday", "Sunday"}
    elif kind == "interval":
        assert trigger.find("t:Repetition/t:Interval", NS).text == "PT17M"
        assert trigger.find("t:Repetition/t:Duration", NS) is None
    else:
        assert trigger.find("t:StartBoundary", NS).text == "2026-10-05T04:27:00"


def test_lead_cross_year_and_runlevel():
    root = xml(schedule(kind="once", date="2027-01-01", time="00:01"), highest=False)
    assert root.find("t:Triggers/t:TimeTrigger/t:StartBoundary", NS).text == "2026-12-31T23:58:00"
    assert root.find("t:Principals/t:Principal/t:RunLevel", NS).text == "LeastPrivilege"


def test_weekday_shift():
    root = xml(schedule(kind="weekly", time="00:01", weekdays=[0, 6]))
    days = root.find("t:Triggers/t:CalendarTrigger/t:ScheduleByWeek/t:DaysOfWeek", NS)
    assert {x.tag.split("}")[1] for x in days} == {"Sunday", "Saturday"}
    boundary = datetime.fromisoformat(root.find("t:Triggers/t:CalendarTrigger/t:StartBoundary", NS).text)
    assert boundary.strftime("%H:%M") == "23:58"
    assert boundary.date() < datetime.now().date()


@pytest.mark.parametrize("kwargs", [{"time": "bad"}, {"kind": "weekly", "weekdays": []},
    {"kind": "interval", "interval_minutes": 0}, {"kind": "once", "date": "bad"}])
def test_invalid_xml(kwargs):
    with pytest.raises(ValueError):
        xml(schedule(**kwargs))


@pytest.fixture
def commands(monkeypatch, tmp_path):
    monkeypatch.setattr(w, "app_root", lambda: tmp_path)
    monkeypatch.setattr(w, "is_admin", lambda: True)
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        assert kwargs["timeout"] == 30
        assert kwargs["capture_output"] is True
        assert kwargs["creationflags"] == subprocess.CREATE_NO_WINDOW
        if "/XML" in args:
            path = Path(args[args.index("/XML") + 1])
            assert path.parent == tmp_path / "runtime"
            ET.fromstring(path.read_text(encoding="utf-16"))
        return subprocess.CompletedProcess(args, 0, b"", b"")
    monkeypatch.setattr(w.subprocess, "run", run)
    return calls


def test_register_and_cleanup(commands, tmp_path):
    assert w.register(schedule(), "python.exe", str(tmp_path))[0]
    args = commands[0]
    assert args[:4] == ["schtasks", "/Create", "/TN", w.task_name("test")]
    assert args[-1] == "/F"
    assert not Path(args[args.index("/XML") + 1]).exists()


def test_nonadmin(commands, monkeypatch):
    monkeypatch.setattr(w, "is_admin", lambda: False)
    ok, message = w.register(schedule(), "python.exe", ".")
    assert not ok and "管理員" in message and not commands
    assert w.register(schedule(), "python.exe", ".", highest=False)[0]


def test_unregister(commands):
    assert w.unregister("test")[0]
    assert commands == [["schtasks", "/Delete", "/TN", w.task_name("test"), "/F"]]


def test_query_and_missing(monkeypatch):
    def run(args, **kwargs):
        if "/Delete" in args:
            return subprocess.CompletedProcess(args, 1, b"", b"missing")
        data = '"\\GachaHub\\中文","date","status"\r\n"\\Other\\x","date","status"'
        return subprocess.CompletedProcess(args, 0, data.encode("cp950"), b"")
    monkeypatch.setattr(w.subprocess, "run", run)
    assert w.list_registered() == [w.task_name("中文")]
    assert w.unregister("absent")[0]
    assert not w.unregister("中文")[0]


def test_sync(commands, monkeypatch):
    monkeypatch.setattr(w, "_query", lambda: [w.task_name("old"), w.task_name("test")])
    schedules = [schedule(wake_computer=True), Schedule(id="off", chain="x", enabled=False, wake_computer=True),
        Schedule(id="no_wake", chain="x")]
    messages = w.sync(schedules, "python.exe", ".")
    assert len(messages) == 2
    assert commands[0][1] == "/Create"
    assert commands[1] == ["schtasks", "/Delete", "/TN", w.task_name("old"), "/F"]


def test_timeout_cleans_file(commands, monkeypatch, tmp_path):
    def timeout(args, **kwargs):
        raise subprocess.TimeoutExpired(args, 30)
    monkeypatch.setattr(w.subprocess, "run", timeout)
    assert not w.register(schedule(), "python.exe", ".")[0]
    assert not list((tmp_path / "runtime").glob("*.xml"))
    assert not w.unregister("test")[0]
    assert w.list_registered() == []
    assert "失敗" in w.sync([], "python.exe", ".")[0]


def test_task_name():
    with pytest.raises(ValueError):
        w.task_name(r"..\Other\task")
