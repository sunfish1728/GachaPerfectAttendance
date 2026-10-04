import json
import struct
import sys
import threading
import time
from pathlib import Path

import psutil
import pytest

from gachahub.adapters import ok_script
from gachahub.adapters.ok_script import OkScriptAdapter, read_version, resolve_task_index
from gachahub.core import process
from gachahub.core.adapter import AdapterError
from gachahub.core.context import Cancelled, RunContext, StepTimeout
from gachahub.core.registry import AdapterRegistry


@pytest.fixture
def installation(tmp_path):
    root = tmp_path / "ok-demo"
    app = root / "data" / "apps" / "ok-demo"
    working = app / "working"
    (working / "src" / "tasks").mkdir(parents=True)
    (working / "logs").mkdir()
    (root / "ok-demo.exe").touch()
    (app / "app.json").write_text(json.dumps({"current_version": "v1.2.3"}), encoding="utf-8")
    (working / "src" / "config.py").write_text(
        'raise RuntimeError("不可執行配置")\nconfig = {"onetime_tasks": [\n'
        '  ["src.tasks.LauncherTask", "LauncherTask"],\n'
        '  # 日常分組，不佔序號\n'
        '  # ["src.tasks.RemovedTask", "RemovedTask"],\n'
        '  ["src.tasks.DailyRoutineTask", "DailyRoutineTask"],\n'
        '  ["src.tasks.FishingTask", "FishingTask"],\n]}\n', encoding="utf-8",
    )
    (working / "src" / "tasks" / "DailyRoutineTask.py").write_text(
        'class OtherTask:\n    name = "不應使用"\n'
        'class DailyRoutineTask:\n    def __init__(self):\n        self.name = "Daily Routine"\n',
        encoding="utf-8",
    )
    (working / "src" / "tasks" / "FishingTask.py").write_text(
        'class FishingTask:\n    def __init__(self):\n        self.name = "釣魚"\n', encoding="utf-8",
    )
    return root


def params(root, **extra):
    return {"install_dir": str(root), "task": "DailyRoutineTask", "startup_grace": 3, **extra}


def log_path(root):
    return root / "data/apps/ok-demo/working/logs/ok-script.log"


def test_index_comments_and_update(installation):
    root = installation
    assert resolve_task_index(root, "ok-demo", "DailyRoutineTask") == 2
    assert resolve_task_index(root, None, "FishingTask") == 3
    path = root / "data/apps/ok-demo/working/src/config.py"
    path.write_text('config = {"onetime_tasks": [["src.tasks.DailyRoutineTask", "DailyRoutineTask"],'
                    '["src.tasks.NewTask", "NewTask"]]}', encoding="utf-8")
    assert resolve_task_index(root, "ok-demo", "DailyRoutineTask") == 1
    assert resolve_task_index(root, "ok-demo", "23") == 23
    with pytest.raises(AdapterError, match="從 1"):
        resolve_task_index(root, "ok-demo", "0")


def test_missing_task_lists_available_names(installation):
    with pytest.raises(AdapterError) as error:
        resolve_task_index(installation, "ok-demo", "RemovedTask")
    assert all(name in str(error.value) for name in ("RemovedTask", "LauncherTask", "DailyRoutineTask", "FishingTask"))


def test_version_and_bad_config(installation):
    assert read_version(installation, "ok-demo") == "v1.2.3"
    (installation / "data/apps/ok-demo/app.json").write_text("invalid", encoding="utf-8")
    assert read_version(installation, None) is None
    path = installation / "data/apps/ok-demo/working/src/config.py"
    path.write_text('config = {"onetime_tasks": get_tasks()}', encoding="utf-8")
    assert OkScriptAdapter().list_tasks(params(installation)) == []
    with pytest.raises(AdapterError, match="無法解析"):
        resolve_task_index(installation, "ok-demo", "DailyRoutineTask")


def test_list_tasks_and_translation(installation):
    # 一筆 gettext 翻譯，使用標準 .mo 格式，避免額外套件或外部工具。
    originals = [b"", b"Daily Routine"]
    translations = [b"Content-Type: text/plain; charset=UTF-8\n", "日常任務".encode("utf-8")]
    data = struct.pack("<7I", 0x950412DE, 0, 2, 28, 44, 0, 0)
    strings = b""
    for value in originals + translations:
        data += struct.pack("<2I", len(value), 60 + len(strings))
        strings += value + b"\0"
    data += strings
    directory = installation / "data/apps/ok-demo/working/i18n/zh_TW/LC_MESSAGES"
    directory.mkdir(parents=True)
    (directory / "ok.mo").write_bytes(data)
    options = OkScriptAdapter().list_tasks(params(installation))
    assert [(o.value, o.label) for o in options] == [
        ("LauncherTask", "LauncherTask"), ("DailyRoutineTask", "日常任務（DailyRoutineTask）"),
        ("FishingTask", "釣魚（FishingTask）"),
    ]
    (directory / "ok.mo").write_bytes(b"broken")
    options = OkScriptAdapter().list_tasks(params(installation))
    assert options[1].label == "Daily Routine（DailyRoutineTask）"


@pytest.fixture
def simulation(installation, monkeypatch):
    root = installation
    launcher = root / "launcher.py"
    worker = root / "worker.py"
    launcher.write_text(
        'import subprocess, sys\nfrom pathlib import Path\n'
        'root = Path(__file__).parent\n'
        'subprocess.Popen([sys.executable, str(root / "worker.py"), *sys.argv[1:]], '
        'creationflags=subprocess.CREATE_NO_WINDOW)\n', encoding="utf-8",
    )
    worker.write_text(
        'import sys, time\nfrom pathlib import Path\n'
        'root = Path(__file__).parent\n'
        'mode = (root / "mode").read_text()\n'
        'log = root / "data/apps/ok-demo/working/logs/ok-script.log"\n'
        '(root / "ready").touch()\n'
        'time.sleep(0.4)\n'
        'if mode == "slow":\n    time.sleep(60)\n'
        'elif mode == "failure":\n'
        '    log.write_text("ERROR TaskExecutor:日常任务 exception stopped\\n", encoding="utf-8")\n'
        '    time.sleep(0.4)\n'
        'elif mode == "recover":\n'
        '    log.write_text("ERROR TaskExecutor DailyRoutineTask:任务运行失败: 喷泉签到 Traceback (most recent call last):\\n", encoding="utf-8")\n'
        '    time.sleep(0.4)\n'
        '    with log.open("a", encoding="utf-8") as f:\n'
        '        f.write("INFO TaskExecutor TaskExecutor:Successfully Executed Task, Exiting Game and App!\\n")\n'
        '    time.sleep(0.4)\n'
        'elif mode == "rotate":\n'
        '    with log.open("a", encoding="utf-8") as f:\n'
        '        f.write("ERROR TaskExecutor:日常任务 exception stopped\\n")\n'
        '    log.rename(log.with_name("ok-script.2026-10-04.log"))\n'
        '    log.write_text("新日誌\\n", encoding="utf-8")\n    time.sleep(0.4)\n'
        'elif mode == "truncate":\n'
        '    log.write_text("ERROR TaskExecutor:task failed\\n", encoding="utf-8")\n'
        '    time.sleep(0.4)\n'
        'elif mode == "replace":\n'
        '    log.unlink()\n'
        '    log.write_text("ERROR TaskExecutor:task failed\\n", encoding="utf-8")\n'
        '    time.sleep(0.4)\n'
        'elif mode == "success":\n'
        '    log.write_bytes(b"\\xff\\nINFO TaskExecutor:Successfully Executed Task, Exiting Game and App!\\n")\n'
        '    time.sleep(0.4)\n'
        'elif mode == "bad_exit":\n    sys.exit(7)\n'
        'else:\n    time.sleep(0.4)\n', encoding="utf-8",
    )
    (root / "mode").write_text("success", encoding="utf-8")
    actual_launch = process.launch
    commands = []

    def launch(command, **kwargs):
        commands.append(command)
        return actual_launch([sys.executable, str(launcher), *command[1:]], hide_window=True, **kwargs)

    def under(directory):
        # 假程式的 Python 位於專案虛擬環境，改以這兩個假腳本路徑識別。
        owned = []
        for proc in psutil.process_iter(["cmdline"]):
            args = proc.info.get("cmdline") or []
            if str(launcher) in args or str(worker) in args:
                owned.append(proc)
        return owned

    def kill_under(directory):
        owned = under(directory)
        for proc in owned:
            try:
                process.kill_tree(proc.pid, timeout=1)
            except psutil.NoSuchProcess:
                pass
        return len(owned)

    monkeypatch.setattr(process, "launch", launch)
    monkeypatch.setattr(process, "processes_under", under)
    monkeypatch.setattr(process, "kill_under", kill_under)
    yield root, commands, under
    kill_under(root)


def context(root):
    return RunContext(root / "work", deadline=time.monotonic() + 8)


def test_stall_cleans_up_after_grace(simulation):
    root, _, under = simulation
    (root / "mode").write_text("slow", encoding="utf-8")
    start = time.monotonic()
    with pytest.raises(AdapterError, match="卡死：日誌已"):
        OkScriptAdapter().run(context(root), params(root, startup_grace=1, stall_minutes=0.001))
    assert time.monotonic() - start >= 1
    assert not under(root)


def test_success_follows_detached_child(simulation):
    root, commands, under = simulation
    log_path(root).write_text("ERROR TaskExecutor:old exception stopped\n", encoding="utf-8")
    logs = []
    ctx = context(root)
    ctx.on_log = logs.append
    assert "日誌確認成功" in OkScriptAdapter().run(ctx, params(root))
    assert commands[0] == [str(root / "ok-demo.exe"), "-t", "2", "-e"]
    assert not under(root)
    assert any("序號 2" in line for line in logs)


@pytest.mark.parametrize("mode", ["failure", "rotate", "truncate", "replace"])
def test_failure_and_log_rotation(simulation, mode):
    root, _, under = simulation
    (root / "mode").write_text(mode, encoding="utf-8")
    log_path(root).write_text("舊資料\n" * 50, encoding="utf-8")
    with pytest.raises(AdapterError, match="任務失敗"):
        OkScriptAdapter().run(context(root), params(root))
    assert not under(root)


def test_subtask_failure_then_success_is_not_killed(simulation):
    # 真實案例：日常任務中噴泉簽到失敗後，腳本仍繼續並成功結束，不可中途終止
    root, _, under = simulation
    (root / "mode").write_text("recover", encoding="utf-8")
    message = OkScriptAdapter().run(context(root), params(root))
    assert "日誌確認成功" in message and "1 則錯誤訊息" in message
    assert not under(root)


@pytest.mark.parametrize("cancel", [True, False])
def test_cancel_and_timeout_kill_children(simulation, cancel):
    root, _, under = simulation
    (root / "mode").write_text("slow", encoding="utf-8")
    ctx = context(root)
    timer = None
    if cancel:
        timer = threading.Timer(1.5, ctx.cancel_event.set)
        timer.start()
    else:
        ctx.deadline = time.monotonic() + 1.5
    try:
        with pytest.raises(Cancelled if cancel else StepTimeout):
            OkScriptAdapter().run(ctx, params(root))
        assert (root / "ready").exists()
        assert not under(root)
    finally:
        if timer:
            timer.cancel()


def test_unknown_result_and_exit_after_false(simulation):
    root, commands, _ = simulation
    (root / "mode").write_text("unknown", encoding="utf-8")
    assert "無法從日誌確認結果" in OkScriptAdapter().run(context(root), params(root, exit_after=False))
    assert "-e" not in commands[0]


def test_grace_without_visible_processes(installation, monkeypatch):
    class Exited:
        pid = 0

        def poll(self):
            return 0

    monkeypatch.setattr(process, "launch", lambda *a, **kw: Exited())
    monkeypatch.setattr(process, "processes_under", lambda *a: [])
    monkeypatch.setattr(process, "kill_under", lambda *a: 0)
    with pytest.raises(AdapterError, match="UAC"):
        OkScriptAdapter().run(context(installation), params(installation, startup_grace=0.1))


def test_existing_process_is_left_alone(installation, monkeypatch):
    monkeypatch.setattr(process, "processes_under", lambda *a: [object()])
    monkeypatch.setattr(process, "launch", lambda *a, **kw: pytest.fail("不可啟動"))
    monkeypatch.setattr(process, "kill_under", lambda *a: pytest.fail("不可關閉既有程序"))
    with pytest.raises(AdapterError, match="已有程序"):
        OkScriptAdapter().run(context(installation), params(installation))


def test_unrelated_process_appearing_during_run_is_left_alone(simulation, monkeypatch):
    root, commands, under = simulation
    (root / "mode").write_text("slow", encoding="utf-8")
    unrelated = psutil.Process()
    actual_kill_tree = process.kill_tree

    def guarded_kill(pid, **kwargs):
        assert pid != unrelated.pid, "不可關閉與本次執行無關的程序"
        return actual_kill_tree(pid, **kwargs)

    monkeypatch.setattr(process, "processes_under", lambda path: under(path) + ([unrelated] if commands else []))
    monkeypatch.setattr(process, "kill_under", lambda *a: pytest.fail("混有無關程序時不可整批關閉"))
    monkeypatch.setattr(process, "kill_tree", guarded_kill)
    ctx = context(root)
    ctx.deadline = time.monotonic() + 1.5
    with pytest.raises(StepTimeout):
        OkScriptAdapter().run(ctx, params(root))
    assert unrelated.is_running()
    assert not under(root)


def test_detection_shallow_and_shortcuts(installation, tmp_path, monkeypatch):
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    (desktop / "異環.lnk").touch()
    deep = tmp_path / "a/b/ok-deep.exe"
    deep.parent.mkdir(parents=True)
    deep.touch()
    monkeypatch.setattr(ok_script, "_search_roots", lambda: ([tmp_path, tmp_path], [desktop]))
    monkeypatch.setattr(ok_script, "_shortcut_target", lambda link: installation / "ok-demo.exe")
    started = time.monotonic()
    assert OkScriptAdapter().detect() == [installation.resolve()]
    assert time.monotonic() - started < 3


def test_registry_yaml_and_validation(installation):
    registry = AdapterRegistry()
    registry.load_dir(Path(__file__).resolve().parents[1] / "adapters")
    adapter = registry.create("ok-nte")
    assert isinstance(adapter, OkScriptAdapter)
    assert adapter.requires_admin
    assert adapter.defaults == {"exe": "ok-nte.exe"}
    for p in ({}, {"install_dir": str(installation)}, params(installation, startup_grace="nan")):
        with pytest.raises(AdapterError):
            adapter.validate(p)
