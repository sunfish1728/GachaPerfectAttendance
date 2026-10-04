"""外部程序工具：啟動、帶超時等待、終止程序樹。"""

from __future__ import annotations

import subprocess
from pathlib import Path

import psutil

from .context import RunContext

POLL_INTERVAL = 0.5


def launch(command: list[str], cwd: str | Path | None = None, hide_window: bool = False) -> subprocess.Popen:
    flags = subprocess.CREATE_NEW_PROCESS_GROUP
    if hide_window:
        flags |= subprocess.CREATE_NO_WINDOW
    return subprocess.Popen(
        command,
        cwd=str(cwd) if cwd else None,
        creationflags=flags,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def kill_tree(pid: int, timeout: float = 5.0) -> None:
    """先 terminate 整棵程序樹，逾時再 kill。"""
    try:
        root = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return
    procs = root.children(recursive=True) + [root]
    for p in procs:
        try:
            p.terminate()
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(procs, timeout=timeout)
    for p in alive:
        try:
            p.kill()
        except psutil.NoSuchProcess:
            pass
    psutil.wait_procs(alive, timeout=timeout)


def wait_exit(ctx: RunContext, proc: subprocess.Popen) -> int:
    """等待程序結束並回傳退出碼；取消／逾時時拋出例外（由呼叫端負責 kill）。"""
    while True:
        code = proc.poll()
        if code is not None:
            return code
        ctx.sleep(POLL_INTERVAL)


def find_processes(name: str) -> list[psutil.Process]:
    name = name.lower()
    out = []
    for p in psutil.process_iter(["name"]):
        if (p.info.get("name") or "").lower() == name:
            out.append(p)
    return out


def kill_by_name(name: str, timeout: float = 5.0) -> int:
    procs = find_processes(name)
    for p in procs:
        kill_tree(p.pid, timeout)
    return len(procs)


def _exe_path(p: psutil.Process) -> Path | None:
    try:
        exe = p.exe()
    except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
        return None
    return Path(exe) if exe else None


def processes_under(directory: str | Path) -> list[psutil.Process]:
    """執行檔位於 directory（含子資料夾）內的程序。
    未提權時無法讀取提權程序的路徑，這些程序會被漏掉——需要管理員權限才可靠。"""
    root = Path(directory).resolve()
    out = []
    for p in psutil.process_iter():
        exe = _exe_path(p)
        if exe is not None and exe.is_relative_to(root):
            out.append(p)
    return out


def kill_under(directory: str | Path, timeout: float = 5.0) -> int:
    procs = processes_under(directory)
    for p in procs:
        kill_tree(p.pid, timeout)
    return len(procs)


def is_admin() -> bool:
    import ctypes

    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False
