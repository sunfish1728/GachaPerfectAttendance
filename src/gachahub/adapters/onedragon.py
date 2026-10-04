"""一條龍命令列適配器；不修改腳本設定，不使用腳本的關機參數。

install_dir 必填；launcher 預設 OneDragon-Launcher.exe。
instance 為逗號分隔的帳號索引；close_game 預設 True。
startup_grace 預設 90 秒，允許提權啟動器與 Python 程序交接。
pre_command 是執行檔與參數的字串列表；pre_timeout 預設 300 秒。
log_file 預設 .log/log.txt；相對路徑以 install_dir 為基準。
success_keywords / fail_keywords 可覆蓋預設的一條龍完成訊息。
成功後最多等待 60 秒；未提供步驟截止時間時，執行上限為一小時。
"""

from __future__ import annotations

import ast
import codecs
import math
import os
import re
import threading
import time
import tomllib
from pathlib import Path
from typing import Any

import psutil
import yaml

from ..core import process
from ..core.adapter import Adapter, AdapterError, TaskOption
from ..core.context import RunContext, capture_if_failing
from ..core.foreground import ForegroundKeeper, allow_foreground
from ..core.watchdog import StallWatch

SUCCESS = "指令[ 一条龙 ] 执行成功"
FAILURE = "指令[ 一条龙 ] 执行失败"
FINISH_WAIT = 60.0
# 一條龍快取了已失效的遊戲視窗句柄（常見於遊戲冷啟動），重啟一條龍即可恢復
WINDOW_LOST = ("游戏窗口未就绪", "切换到游戏窗口失败")
POLL_INTERVAL = 0.25
RELAUNCH_DELAY = 5.0


class _LogTail:
    """只讀本次新增內容；換檔或縮小時重設，保留跨次讀取的 UTF-8 字元。"""

    def __init__(self, path: Path):
        self.path = path
        self.identity = None
        self.offset = 0
        self.decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self.buffer = ""
        try:
            stat = path.stat()
            self.identity = (stat.st_dev, stat.st_ino, stat.st_ctime_ns)
            self.offset = stat.st_size
        except OSError:
            pass

    def read(self) -> str:
        try:
            with self.path.open("rb") as stream:
                # Windows 的 fstat 與 Path.stat 可能提供不同的檔案編號。
                stat = self.path.stat()
                identity = (stat.st_dev, stat.st_ino, stat.st_ctime_ns)
                if identity != self.identity or stat.st_size < self.offset:
                    self.offset = 0
                    self.buffer = ""
                    self.decoder.reset()
                self.identity = identity
                stream.seek(self.offset)
                data = stream.read()
                self.offset += len(data)
        except OSError:
            return ""
        self.buffer = (self.buffer + self.decoder.decode(data))[-65536:]
        return self.buffer


def read_version(install_dir: str | Path) -> str | None:
    """讀取專案版本；不匯入或執行安裝資料夾中的程式。"""
    root = Path(install_dir)
    try:
        with (root / "pyproject.toml").open("rb") as stream:
            version = tomllib.load(stream).get("project", {}).get("version")
        if isinstance(version, str) and version:
            return version
    except (OSError, ValueError):
        pass
    try:
        tree = ast.parse((root / "src/one_dragon/version.py").read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "__version__" for t in node.targets
            ):
                value = ast.literal_eval(node.value)
                return value if isinstance(value, str) else None
    except (OSError, ValueError, SyntaxError):
        pass
    return None


def _detect_roots() -> tuple[list[Path], list[Path]]:
    import ctypes
    import winreg

    drives = [Path(f"{letter}:/") for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
              if ctypes.windll.kernel32.GetDriveTypeW(f"{letter}:\\") in (2, 3)]
    home = Path.home()
    folders = [home / name for name in ("Desktop", "Downloads", "Documents")]
    desktops = [folders[0]]
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                           r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as key:
            for name in ("Desktop", "Personal", "{374DE290-123F-4565-9164-39C4925E467B}"):
                try:
                    folder = Path(os.path.expandvars(winreg.QueryValueEx(key, name)[0]))
                    folders.append(folder)
                    if name == "Desktop":
                        desktops.append(folder)
                except OSError:
                    pass
    except OSError:
        pass
    if os.environ.get("PUBLIC"):
        desktops.append(Path(os.environ["PUBLIC"]) / "Desktop")
    return list(dict.fromkeys(drives + folders)), list(dict.fromkeys(desktops))


def _shortcut_dirs(desktops: list[Path]):
    # 動態 COM 不產生型別快取，僅讀取捷徑，不執行目標。
    import comtypes
    from comtypes.client import CreateObject

    # 不呼叫 CoUninitialize：shell/link 物件在產生器結束後才釋放，先反初始化會造成存取違規而讓整個程式崩潰
    comtypes.CoInitialize()
    shell = CreateObject("WScript.Shell", dynamic=True)
    for desktop in desktops:
        for path in desktop.glob("*.lnk"):
            try:
                link = shell.CreateShortcut(str(path))
                if link.WorkingDirectory:
                    yield Path(os.path.expandvars(link.WorkingDirectory))
                if link.TargetPath:
                    yield Path(os.path.expandvars(link.TargetPath)).parent
                match = re.search(r'-File\s+(?:"([^"]+)"|(\S+))', link.Arguments, re.I)
                if match:
                    yield Path(os.path.expandvars(match.group(1) or match.group(2))).parent
            except Exception:
                continue


class OneDragonAdapter(Adapter):
    id = "onedragon"
    display_name = "一條龍系列"
    requires_admin = True
    uses_install_dir = True
    task_param = "instance"
    task_label = "帳號實例"
    bool_options = [("close_game", "跑完關閉遊戲", True)]

    def validate(self, params: dict[str, Any]) -> None:
        p = self.merged(params)
        if not p.get("install_dir"):
            raise AdapterError("缺少 install_dir（安裝資料夾）")
        root = Path(p["install_dir"])
        if not root.is_dir() or not (root / p.get("launcher", "OneDragon-Launcher.exe")).is_file():
            raise AdapterError("找不到一條龍安裝資料夾或啟動器")
        for key, default in (("startup_grace", 90), ("pre_timeout", 300)):
            try:
                number = float(p.get(key, default))
                if not math.isfinite(number) or number <= 0:
                    raise ValueError()
            except (TypeError, ValueError):
                raise AdapterError(f"{key} 必須是正數秒數") from None
        for key in ("pre_command", "success_keywords", "fail_keywords"):
            value = p.get(key, [])
            if not isinstance(value, list) or any(not isinstance(x, str) or not x for x in value):
                raise AdapterError(f"{key} 必須是非空字串的列表")
        try:
            StallWatch([], p.get("stall_minutes", 20))
        except (TypeError, ValueError):
            raise AdapterError("stall_minutes 必須是有限的分鐘數（0 或負數停用）") from None

    def build_command(self, p: dict[str, Any]) -> list[str]:
        cmd = [str(Path(p["install_dir"]) / p.get("launcher", "OneDragon-Launcher.exe")), "-o"]
        if p.get("close_game", True):
            cmd.append("-c")
        instance = str(p.get("instance", "")).strip()
        if instance:
            cmd.extend(["-i", instance])
        return cmd

    def run(self, ctx: RunContext, params: dict[str, Any]) -> str:
        self.validate(params)
        p = self.merged(params)
        root = Path(p["install_dir"]).resolve()
        ctx.check()
        if process.processes_under(root):
            raise AdapterError("這個安裝資料夾已有程序執行，請先關閉一條龍再試")
        tracked: dict[tuple[int, float], psutil.Process] = {}
        launched = []
        old_deadline = ctx.deadline
        # 直接呼叫適配器也有上限；runner 提供的步驟逾時優先。
        if ctx.deadline is None:
            ctx.deadline = time.monotonic() + 3600

        def remember(items):
            for item in items:
                try:
                    tracked[(item.pid, item.create_time())] = item
                    for child in item.children(recursive=True):
                        tracked[(child.pid, child.create_time())] = child
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass

        def active():
            remember(process.processes_under(root))
            for proc in launched:
                if proc.poll() is None:
                    try:
                        remember([psutil.Process(proc.pid)])
                    except psutil.NoSuchProcess:
                        pass
            alive = []
            for item in list(tracked.values()):
                try:
                    if item.is_running() and item.status() != psutil.STATUS_ZOMBIE:
                        alive.append(item)
                except psutil.NoSuchProcess:
                    pass
            remember(alive)
            return alive

        def stop():
            # 按 PID 與建立時間清理，避免 PID 重用或殺到別的安裝。
            for item in active():
                try:
                    if item.is_running():
                        process.kill_tree(item.pid)
                except psutil.NoSuchProcess:
                    pass

        try:
            if p.get("pre_command"):
                ctx.log("執行一條龍前置命令")
                try:
                    pre = process.launch(p["pre_command"], cwd=root, hide_window=True)
                except OSError as exc:
                    ctx.log(f"警告：前置命令無法啟動（{exc}），繼續執行")
                else:
                    launched.append(pre)
                    end = time.monotonic() + float(p.get("pre_timeout", 300))
                    while pre.poll() is None:
                        ctx.check()
                        active()
                        if time.monotonic() >= end:
                            stop()
                            ctx.log("警告：前置命令逾時，繼續執行")
                            break
                        ctx.sleep(POLL_INTERVAL)
                    if pre.returncode not in (None, 0):
                        ctx.log(f"警告：前置命令退出碼 {pre.returncode}，繼續執行")
                    stop()
            log_path = Path(p.get("log_file") or root / ".log/log.txt")
            if not log_path.is_absolute():
                log_path = root / log_path
            relaunches = int(p.get("window_retries", 1))
            while True:
                tail = _LogTail(log_path)
                cmd = self.build_command({**p, "install_dir": str(root)})
                ctx.check()
                ctx.log(f"啟動一條龍：{' '.join(cmd)}")
                allow_foreground()
                try:
                    launched.append(process.launch(cmd, cwd=root, hide_window=True))
                except OSError as exc:
                    raise AdapterError(f"一條龍啟動失敗：{exc}") from exc
                grace_end = time.monotonic() + float(p.get("startup_grace", 90))
                watch = StallWatch([root / ".log"], p.get("stall_minutes", 20))
                keeper = ForegroundKeeper(list(p.get("game_processes") or []), on_log=ctx.log)
                seen = False
                seen_worker = False
                window_lost = False
                success_at = None
                while True:
                    ctx.check()
                    keeper.tick()
                    alive = active()
                    seen = seen or bool(alive)
                    for item in alive:
                        try:
                            if Path(item.exe()).resolve() != Path(cmd[0]).resolve():
                                seen_worker = True
                        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                            pass
                    content = tail.read()
                    window_lost = window_lost or any(m in content for m in WINDOW_LOST)
                    failed = next((k for k in p.get("fail_keywords", [FAILURE]) if k in content), None)
                    if failed and window_lost and relaunches > 0:
                        relaunches -= 1
                        ctx.log("一條龍抓到失效的遊戲視窗（遊戲冷啟動時偶發），重新啟動一條龍")
                        stop()
                        launched.clear()
                        tracked.clear()
                        ctx.sleep(RELAUNCH_DELAY)
                        break
                    if failed:
                        raise AdapterError(f"一條龍日誌出現失敗關鍵字：{failed}")
                    if success_at is None and any(k in content for k in p.get("success_keywords", [SUCCESS])):
                        success_at = time.monotonic()
                        ctx.log("一條龍執行成功，等待程序結束")
                    if success_at is not None:
                        if not alive:
                            return "一條龍執行成功"
                        if time.monotonic() - success_at >= FINISH_WAIT:
                            ctx.log("成功後程序仍未退出，清理本次程序")
                            stop()
                            return "一條龍執行成功（已清理程序）"
                    elif not alive:
                        # 提權啟動器會先退出，再由另一個程序接手；寬限期內允許空窗。
                        if seen_worker or (seen and time.monotonic() >= grace_end):
                            raise AdapterError("一條龍程序已結束，但未讀到成功關鍵字")
                        if time.monotonic() >= grace_end:
                            raise AdapterError("寬限期內未看到一條龍程序，請檢查 UAC 提示與管理員權限")
                    idle = watch.check()
                    if (success_at is None and time.monotonic() >= grace_end
                            and idle is not None and idle >= watch.stall_minutes * 60):
                        raise AdapterError(f"卡死：日誌已 {idle / 60:.1f} 分鐘沒有更新")
                    ctx.sleep(POLL_INTERVAL)
        finally:
            capture_if_failing(ctx)
            try:
                stop()
            finally:
                ctx.deadline = old_deadline

    def list_tasks(self, params: dict[str, Any]) -> list[TaskOption]:
        try:
            p = self.merged(params)
            root = Path(p["install_dir"])
            data = yaml.safe_load((root / "config/one_dragon.yml").read_text(encoding="utf-8"))
            instances = data["instance_list"]
            if not isinstance(instances, list):
                return []
            return [TaskOption("", "全部／預設實例"), *[
                TaskOption(str(item["idx"]), str(item["name"])) for item in instances
            ]]
        except (OSError, KeyError, TypeError, ValueError, yaml.YAMLError):
            return []

    def script_version(self, params: dict) -> str | None:
        try:
            return read_version(self.merged(params)["install_dir"])
        except Exception:
            return None

    def detect(self) -> list[Path]:
        found: set[Path] = set()
        end = time.monotonic() + 2.5

        def add(path):
            if (path / "OneDragon-Launcher.exe").is_file():
                found.add(path.resolve())

        def scan():
            try:
                roots, desktops = _detect_roots()
                for root in roots:
                    if time.monotonic() >= end:
                        return
                    try:
                        add(root)
                        for entry in root.iterdir():
                            if time.monotonic() >= end:
                                return
                            if entry.is_dir():
                                add(entry)
                    except OSError:
                        continue
                for path in _shortcut_dirs(desktops):
                    if time.monotonic() >= end:
                        return
                    add(path)
            except Exception:
                pass

        worker = threading.Thread(target=scan, daemon=True)
        worker.start()
        worker.join(max(0, end - time.monotonic()))
        return sorted(found.copy(), key=str)
