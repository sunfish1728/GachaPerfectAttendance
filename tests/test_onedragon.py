import json
import sys
import threading
import time
from types import SimpleNamespace
from pathlib import Path

import psutil
import pytest

from gachahub.adapters import onedragon
from gachahub.adapters.onedragon import FAILURE, SUCCESS, OneDragonAdapter, read_version
from gachahub.core import process
from gachahub.core.adapter import AdapterError
from gachahub.core.context import Cancelled, RunContext, StepTimeout
from gachahub.core.registry import AdapterRegistry


@pytest.fixture
def fake(tmp_path, monkeypatch):
    root = tmp_path / "install"
    root.mkdir()
    (root / "OneDragon-Launcher.exe").touch()
    (root / ".log").mkdir()
    script = root / "fake.py"
    script.write_text("", encoding="utf-8")
    procs = []
    commands = []
    real_launch = process.launch

    def launch(command, **kwargs):
        commands.append(command)
        if Path(command[0]).name == "OneDragon-Launcher.exe":
            command = [sys.executable, str(script), *command[1:]]
        proc = real_launch(command, **kwargs)
        procs.append(proc)
        return proc

    def under(directory):
        assert Path(directory) == root
        result = []
        for proc in procs:
            if proc.poll() is None:
                try:
                    item = psutil.Process(proc.pid)
                    result.extend([item, *item.children(recursive=True)])
                except psutil.NoSuchProcess:
                    pass
        return result

    monkeypatch.setattr(process, "launch", launch)
    monkeypatch.setattr(process, "processes_under", under)
    monkeypatch.setattr(onedragon, "POLL_INTERVAL", 0.03)
    messages = []
    ctx = RunContext(work_dir=tmp_path / "work", on_log=messages.append,
                     deadline=time.monotonic() + 10)
    params = {"install_dir": str(root), "startup_grace": 0.25}
    yield root, script, ctx, params, procs, commands, messages
    for proc in procs:
        if proc.poll() is None:
            process.kill_tree(proc.pid)


def program(script, keyword=SUCCESS, *, linger=False, rotate=None):
    script.write_text(
        "import pathlib,sys,time,json,subprocess\n"
        "root=pathlib.Path(__file__).parent\n"
        "(root/'args.json').write_text(json.dumps(sys.argv[1:]))\n"
        "p=root/'.log/log.txt'\n"
        "time.sleep(0.12)\n"
        + ("p.rename(p.with_name('log.txt.2026-10-04'))\n" if rotate == "rename" else "")
        + ("p.write_bytes(b'')\ntime.sleep(0.08)\n" if rotate == "truncate" else "")
        + f"with p.open('a',encoding='utf-8') as f: f.write({keyword!r}+'\\n')\n"
        + ("time.sleep(60)\n" if linger else ""), encoding="utf-8",
    )


def test_command():
    adapter = OneDragonAdapter()
    p = {"install_dir": "C:/fake", "instance": "1,2"}
    assert adapter.build_command(p)[1:] == ["-o", "-c", "-i", "1,2"]
    assert adapter.build_command({**p, "instance": "", "close_game": False})[1:] == ["-o"]
    assert adapter.requires_admin


def test_stall_cleans_up_after_grace(fake):
    _, script, ctx, params, procs, _, _ = fake
    script.write_text("import time; time.sleep(60)", encoding="utf-8")
    start = time.monotonic()
    with pytest.raises(AdapterError, match="卡死：日誌已"):
        OneDragonAdapter().run(ctx, {**params, "startup_grace": 0.5, "stall_minutes": 0.001})
    assert time.monotonic() - start >= 0.5
    assert all(proc.poll() is not None for proc in procs)


def test_success_ignores_old_log(fake):
    root, script, ctx, params, procs, commands, _ = fake
    (root / ".log/log.txt").write_text(FAILURE + "\n" + SUCCESS, encoding="utf-8")
    program(script)
    assert "成功" in OneDragonAdapter().run(ctx, {**params, "instance": "1,2"})
    assert json.loads((root / "args.json").read_text()) == ["-o", "-c", "-i", "1,2"]
    assert all(p.poll() is not None for p in procs)


def test_failure(fake):
    _, script, ctx, params, procs, _, _ = fake
    program(script, FAILURE, linger=True)
    with pytest.raises(AdapterError, match="失敗關鍵字"):
        OneDragonAdapter().run(ctx, params)
    assert all(p.poll() is not None for p in procs)


@pytest.mark.parametrize("rotation", ["rename", "truncate"])
def test_rotation(fake, rotation):
    root, script, ctx, params, *_ = fake
    (root / ".log/log.txt").write_text("舊資料" * 1000, encoding="utf-8")
    program(script, rotate=rotation)
    assert "成功" in OneDragonAdapter().run(ctx, params)


def test_rotation_larger_replacement_and_split_utf8(tmp_path):
    path = tmp_path / "log.txt"
    path.write_bytes(b"old")
    tail = onedragon._LogTail(path)
    path.rename(tmp_path / "log.txt.old")
    encoded = SUCCESS.encode("utf-8")
    path.write_bytes(encoded[:5])
    assert SUCCESS not in tail.read()
    with path.open("ab") as stream:
        stream.write(encoded[5:])
    assert SUCCESS in tail.read()


def test_success_kills_lingering_program(fake, monkeypatch):
    _, script, ctx, params, procs, _, messages = fake
    program(script, linger=True)
    monkeypatch.setattr(onedragon, "FINISH_WAIT", 0.15)
    assert "已清理" in OneDragonAdapter().run(ctx, params)
    assert all(p.poll() is not None for p in procs)
    assert any("仍未退出" in msg for msg in messages)


@pytest.mark.parametrize("code", [0, 3])
def test_pre_command(fake, code):
    root, script, ctx, params, _, commands, messages = fake
    program(script)
    marker = root / "pre.txt"
    pre = [sys.executable, "-c",
           f"import pathlib;pathlib.Path({str(marker)!r}).write_text('ok');raise SystemExit({code})"]
    assert "成功" in OneDragonAdapter().run(ctx, {**params, "pre_command": pre})
    assert marker.read_text() == "ok"
    assert commands[0] == pre
    assert any("退出碼 3" in m for m in messages) == (code == 3)


def test_pre_launch_error_and_timeout(fake, monkeypatch):
    _, script, ctx, params, procs, _, messages = fake
    program(script)
    adapter = OneDragonAdapter()
    assert "成功" in adapter.run(ctx, {**params, "pre_command": ["missing-command-12345.exe"]})
    assert any("無法啟動" in m for m in messages)
    assert "成功" in adapter.run(ctx, {**params, "pre_command": [sys.executable, "-c", "import time;time.sleep(60)"],
                                          "pre_timeout": 0.15})
    assert any("前置命令逾時" in m for m in messages)
    assert all(p.poll() is not None for p in procs)


@pytest.mark.parametrize("pre", [False, True])
@pytest.mark.parametrize("cancel", [False, True])
def test_cancel_and_timeout(fake, pre, cancel):
    _, script, ctx, params, procs, _, _ = fake
    program(script, keyword="執行中", linger=True)
    if pre:
        params["pre_command"] = [sys.executable, "-c", "import time;time.sleep(60)"]
    timer = None
    if cancel:
        timer = threading.Timer(0.3, ctx.cancel_event.set)
        timer.start()
    else:
        ctx.deadline = time.monotonic() + 0.3
    try:
        with pytest.raises(Cancelled if cancel else StepTimeout):
            OneDragonAdapter().run(ctx, params)
        assert procs and all(p.poll() is not None for p in procs)
    finally:
        if timer:
            timer.cancel()


def test_no_process_in_grace(fake, monkeypatch):
    _, _, ctx, params, _, _, _ = fake

    class Gone:
        def poll(self):
            return 0

    monkeypatch.setattr(process, "launch", lambda *a, **kw: Gone())
    with pytest.raises(AdapterError, match="UAC.*管理員"):
        OneDragonAdapter().run(ctx, params)


def test_exit_without_keyword(fake):
    _, script, ctx, params, *_ = fake
    program(script, keyword="結束了")
    with pytest.raises(AdapterError, match="未讀到成功"):
        OneDragonAdapter().run(ctx, params)


def test_existing_program_not_killed(fake, monkeypatch):
    _, _, ctx, params, _, commands, _ = fake
    monkeypatch.setattr(process, "processes_under", lambda root: [object()])
    monkeypatch.setattr(process, "kill_tree", lambda pid: pytest.fail("不可清理既有程序"))
    with pytest.raises(AdapterError, match="已有程序"):
        OneDragonAdapter().run(ctx, params)
    assert commands == []


def test_uac_handoff(fake, monkeypatch):
    root, script, ctx, params, *_ = fake
    child = root / "worker.py"
    pid_file = root / "worker.pid"
    program(child)
    script.write_text(
        "import sys,subprocess,pathlib\n"
        f"p=subprocess.Popen([sys.executable,{str(child)!r}])\n"
        f"pathlib.Path({str(pid_file)!r}).write_text(str(p.pid))\n", encoding="utf-8")
    original_under = process.processes_under

    def under(directory):
        result = original_under(directory)
        if pid_file.exists():
            try:
                worker = psutil.Process(int(pid_file.read_text()))
                if worker.is_running():
                    result.append(worker)
            except psutil.NoSuchProcess:
                pass
        return result

    monkeypatch.setattr(process, "processes_under", under)
    assert "成功" in OneDragonAdapter().run(ctx, params)
    assert not psutil.pid_exists(int(pid_file.read_text()))


def test_overridden_keywords(fake):
    _, script, ctx, params, *_ = fake
    program(script, keyword="其他一條龍完成")
    assert "成功" in OneDragonAdapter().run(ctx, {
        **params, "success_keywords": ["其他一條龍完成"], "fail_keywords": ["其他一條龍失敗"]})


def test_child_cleanup(fake):
    root, script, ctx, params, *_ = fake
    child_pid = root / "child.pid"
    script.write_text(
        "import subprocess,sys,pathlib,time\n"
        "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])\n"
        f"pathlib.Path({str(child_pid)!r}).write_text(str(child.pid))\n"
        "time.sleep(60)\n", encoding="utf-8")
    ctx.deadline = time.monotonic() + 0.5
    with pytest.raises(StepTimeout):
        OneDragonAdapter().run(ctx, params)
    assert child_pid.exists()
    assert not psutil.pid_exists(int(child_pid.read_text()))


def test_list_tasks_and_version(tmp_path):
    adapter = OneDragonAdapter(defaults={"install_dir": str(tmp_path)})
    assert adapter.list_tasks({}) == []
    (tmp_path / "config").mkdir()
    config = tmp_path / "config/one_dragon.yml"
    config.write_text("instance_list:\n- idx: 1\n  name: 主帳號\n- idx: 2\n  name: 小號\n", encoding="utf-8")
    assert [(t.value, t.label) for t in adapter.list_tasks({})] == [
        ("", "全部／預設實例"), ("1", "主帳號"), ("2", "小號")]
    config.write_text("instance_list: [", encoding="utf-8")
    assert adapter.list_tasks({}) == []
    assert read_version(tmp_path) is None
    (tmp_path / "pyproject.toml").write_text('[project]\nversion="2.0.0"', encoding="utf-8")
    assert read_version(tmp_path) == "2.0.0"
    (tmp_path / "pyproject.toml").unlink()
    (tmp_path / "src/one_dragon").mkdir(parents=True)
    (tmp_path / "src/one_dragon/version.py").write_text('__version__ = "v1.2.3"', encoding="utf-8")
    assert read_version(tmp_path) == "v1.2.3"


def test_detect_shallow_and_shortcut_cwd(tmp_path, monkeypatch):
    ordinary = tmp_path / "drive/zzz"
    shortcut = tmp_path / "elsewhere/deep/install"
    for root in (ordinary, shortcut):
        root.mkdir(parents=True)
        (root / "OneDragon-Launcher.exe").touch()
    monkeypatch.setattr(onedragon, "_detect_roots", lambda: ([ordinary.parent], []))
    monkeypatch.setattr(onedragon, "_shortcut_dirs", lambda desktops: iter([shortcut]))
    assert set(OneDragonAdapter().detect()) == {ordinary, shortcut}


def test_shortcut_powershell_and_working_directory(tmp_path, monkeypatch):
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    (desktop / "一條龍.lnk").touch()
    cwd = tmp_path / "install"
    ps1 = tmp_path / "addon folder/launch.ps1"
    shortcut = SimpleNamespace(WorkingDirectory=str(cwd), TargetPath="C:/Windows/powershell.exe",
                               Arguments=f'-NoProfile -File "{ps1}"')
    shell = SimpleNamespace(CreateShortcut=lambda path: shortcut)
    monkeypatch.setitem(sys.modules, "comtypes", SimpleNamespace(CoInitialize=lambda: None,
                                                                  CoUninitialize=lambda: None))
    monkeypatch.setitem(sys.modules, "comtypes.client", SimpleNamespace(CreateObject=lambda *a, **kw: shell))
    paths = list(onedragon._shortcut_dirs([desktop]))
    assert cwd in paths
    assert ps1.parent in paths


def test_detect_timeout(monkeypatch):
    release = threading.Event()

    def blocked_roots():
        release.wait(5)
        return [], []

    monkeypatch.setattr(onedragon, "_detect_roots", blocked_roots)
    start = time.monotonic()
    try:
        assert OneDragonAdapter().detect() == []
        assert time.monotonic() - start < 3
    finally:
        release.set()


def test_registry_yaml():
    registry = AdapterRegistry()
    registry.load_dir(Path(__file__).resolve().parents[1] / "adapters")
    assert isinstance(registry.create("zzz-onedragon"), OneDragonAdapter)


@pytest.mark.parametrize("change", [{"pre_command": "bad"}, {"pre_timeout": 0},
                                     {"startup_grace": float("nan")}, {"fail_keywords": "bad"}])
def test_validation(fake, change):
    _, _, ctx, params, _, commands, _ = fake
    with pytest.raises(AdapterError):
        OneDragonAdapter().run(ctx, {**params, **change})
    assert commands == []


def _window_lost_program(script):
    """第一次執行模擬一條龍抓到失效視窗而失敗（之後不退出），第二次成功。"""
    script.write_text(
        "import pathlib,time\n"
        "root=pathlib.Path(__file__).parent\n"
        "n=root/'runs.txt'\n"
        "k=int(n.read_text()) if n.exists() else 0\n"
        "n.write_text(str(k+1))\n"
        "time.sleep(0.12)\n"
        "p=root/'.log/log.txt'\n"
        "with p.open('a',encoding='utf-8') as f:\n"
        f"    f.write(('RuntimeError: 游戏窗口未就绪\\n'+{FAILURE!r}+'\\n') if k==0 else {SUCCESS!r}+'\\n')\n"
        "time.sleep(60 if k==0 else 0)\n", encoding="utf-8")


def test_relaunch_once_when_game_window_lost(fake, monkeypatch):
    root, script, ctx, params, procs, commands, messages = fake
    monkeypatch.setattr(onedragon, "RELAUNCH_DELAY", 0.05)
    _window_lost_program(script)
    assert "成功" in OneDragonAdapter().run(ctx, params)
    assert (root / "runs.txt").read_text() == "2"
    assert any("重新啟動一條龍" in m for m in messages)
    assert all(p.poll() is not None for p in procs)


def test_window_lost_relaunch_is_limited(fake, monkeypatch):
    root, script, ctx, params, *_ = fake
    monkeypatch.setattr(onedragon, "RELAUNCH_DELAY", 0.05)
    _window_lost_program(script)
    with pytest.raises(AdapterError, match="失敗關鍵字"):
        OneDragonAdapter().run(ctx, {**params, "window_retries": 0})
    assert (root / "runs.txt").read_text() == "1"


def test_max_duration_stops_onedragon(fake):
    from gachahub.core.context import DurationReached
    _, script, ctx, params, procs, _, _ = fake
    script.write_text("import time; time.sleep(60)", encoding="utf-8")
    ctx.limit_at = time.monotonic() + 0.6
    with pytest.raises(DurationReached):
        OneDragonAdapter().run(ctx, params)
    assert all(p.poll() is not None for p in procs)
