"""OK 系列：由目前配置解析任務序號，追蹤安裝目錄內的程序及腳本日誌。"""

from __future__ import annotations

import ast
import codecs
import ctypes
import gettext
import json
import math
import os
import re
import struct
import time
from pathlib import Path
from typing import Any

import psutil

from ..core import process
from ..core.adapter import Adapter, AdapterError, TaskOption
from ..core.context import RunContext, capture_if_failing
from ..core.watchdog import StallWatch


def _child(root: Path, name: str) -> Path:
    if not name or Path(name).name != name or name in (".", ".."):
        raise AdapterError(f"請填寫單一檔案／資料夾名稱：{name}")
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()):
        raise AdapterError(f"路徑超出安裝資料夾：{name}")
    return path


def _app_dir(install_dir: str | Path, app: str | None, exe: Path | None = None) -> Path:
    apps = Path(install_dir) / "data" / "apps"
    if app:
        selected = _child(apps, app)
    elif exe and (apps / exe.stem).is_dir():
        selected = _child(apps, exe.stem)
    else:
        choices = [p for p in apps.iterdir() if p.is_dir()] if apps.is_dir() else []
        if len(choices) != 1:
            raise AdapterError("無法判斷 app，請指定 data/apps 下的子資料夾名稱")
        selected = choices[0]
    if not selected.is_dir():
        raise AdapterError(f"找不到腳本資料夾：{selected}")
    return selected


def _tasks(app_dir: Path) -> list[tuple[str, str]]:
    path = app_dir / "working" / "src" / "config.py"
    try:
        tree = ast.parse(path.read_text(encoding="utf-8-sig", errors="replace"))
        node = None
        # 只讀配置的文字結構，絕不匯入或執行腳本。
        for statement in tree.body:
            if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
                continue
            targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
            for target in targets:
                if isinstance(target, ast.Name) and target.id == "config" and isinstance(statement.value, ast.Dict):
                    for key, value in zip(statement.value.keys, statement.value.values):
                        if isinstance(key, ast.Constant) and key.value == "onetime_tasks":
                            node = value
                elif (isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name)
                      and target.value.id == "config" and isinstance(target.slice, ast.Constant)
                      and target.slice.value == "onetime_tasks"):
                    node = statement.value
        entries = ast.literal_eval(node) if node is not None else None
        if not isinstance(entries, (list, tuple)):
            raise ValueError("onetime_tasks 必須是靜態列表")
        tasks = []
        for entry in entries:
            if (not isinstance(entry, (list, tuple)) or len(entry) < 2
                    or not all(isinstance(s, str) and s for s in entry[:2])):
                raise ValueError("onetime_tasks 項目必須包含模組與類別名稱")
            tasks.append((entry[0], entry[1]))
        return tasks
    except (OSError, SyntaxError, ValueError, TypeError) as e:
        raise AdapterError(f"無法解析任務配置 {path}：{e}") from e


def resolve_task_index(install_dir: str | Path, app: str | None, task: str) -> int:
    """每次讀取目前列表，回傳從 1 開始的序號。數字可直接指定序號。"""
    task = str(task).strip()
    if re.fullmatch(r"[0-9]+", task):
        index = int(task)
        if index < 1:
            raise AdapterError("任務序號必須從 1 開始")
        return index
    tasks = _tasks(_app_dir(install_dir, app))
    matches = [i for i, (_, name) in enumerate(tasks, 1) if name == task]
    if len(matches) == 1:
        return matches[0]
    available = "、".join(name for _, name in tasks) or "（空列表）"
    reason = "任務名稱重複，請使用數字序號" if matches else f"找不到任務：{task}"
    raise AdapterError(f"{reason}。目前可用任務：{available}")


def read_version(install_dir: str | Path, app: str | None) -> str | None:
    """讀取 pyappify 記錄的目前版本；檔案不可讀時回傳 None。"""
    try:
        data = json.loads((_app_dir(install_dir, app) / "app.json").read_text(encoding="utf-8-sig", errors="replace"))
        value = data.get("current_version")
        return value if isinstance(value, str) else None
    except (AdapterError, OSError, ValueError, AttributeError):
        return None


def _task_name(working: Path, module: str, name: str) -> str:
    path = working.joinpath(*module.split(".")).with_suffix(".py").resolve()
    if not path.is_relative_to(working.resolve()):
        return name
    try:
        tree = ast.parse(path.read_text(encoding="utf-8-sig", errors="replace"))
        for cls in tree.body:
            if not isinstance(cls, ast.ClassDef) or cls.name != name:
                continue
            for node in ast.walk(cls):
                if not isinstance(node, ast.Assign):
                    continue
                if any(isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name)
                       and t.value.id == "self" and t.attr == "name" for t in node.targets):
                    value = node.value
                    if isinstance(value, ast.Call) and value.args:
                        value = value.args[0]
                    if isinstance(value, ast.Constant) and isinstance(value.value, str):
                        return value.value
    except (OSError, SyntaxError, ValueError):
        pass
    return name


class _LogTail:
    """按檔案身分追蹤位移；輪替改名後仍讀取舊檔末尾，新檔從頭讀。"""

    def __init__(self, directory: Path):
        self.directory = directory
        self.states: dict[tuple[int, int], tuple[int, Any, str, bytes]] = {}
        for path in self._paths():
            try:
                with path.open("rb") as stream:
                    stat = os.fstat(stream.fileno())
                    stream.seek(max(0, stat.st_size - 64))
                    anchor = stream.read()
                self.states[(stat.st_dev, stat.st_ino)] = (stat.st_size, self._decoder(), "", anchor)
            except OSError:
                pass

    @staticmethod
    def _decoder():
        return codecs.getincrementaldecoder("utf-8")(errors="replace")

    def _paths(self) -> list[Path]:
        try:
            return sorted(self.directory.glob("ok-script*.log*"))
        except OSError:
            return []

    def read(self) -> str:
        chunks = []
        for path in self._paths():
            try:
                with path.open("rb") as stream:
                    stat = os.fstat(stream.fileno())
                    key = (stat.st_dev, stat.st_ino)
                    offset, decoder, buf, anchor = self.states.get(key, (0, self._decoder(), "", b""))
                    stream.seek(max(0, offset - 64))
                    previous = stream.read(min(offset, 64))
                    if stat.st_size < offset or previous != anchor:
                        offset, decoder, buf = 0, self._decoder(), ""
                    stream.seek(offset)
                    data = stream.read()
                    stream.seek(max(0, offset + len(data) - 64))
                    anchor = stream.read(min(offset + len(data), 64))
                if data:
                    combined = buf + decoder.decode(data)
                    chunks.append(combined)
                    buf = combined[-65536:]
                self.states[key] = (offset + len(data), decoder, buf, anchor)
            except OSError:
                continue
        return "\n".join(chunks)


# 成功標誌（實際日誌：TaskExecutor:Successfully Executed Task, Exiting Game and App!）
_SUCCESS = ("successfully executed task", "end running onetime_task", "task done")
# 失敗訊息只作為參考：日常任務中的子任務失敗（例如「任务运行失败: 喷泉签到 Traceback」）
# 之後腳本仍會繼續並成功結束，所以出現時不可中斷腳本，只在最後沒有成功標誌時判定失敗。
_FAIL = re.compile(
    r"exception stopped|traceback \(most recent call last\)|uncaught exception|"
    r"do_start[^\n]*exception|start failed|start game timeout|task (?:failed|error)|"
    r"\bCRITICAL\b|\bERROR\b[^\n]*TaskExecutor:", re.IGNORECASE,
)


def _belongs_to(child: psutil.Process, pids: set[int], candidates: set[int]) -> bool:
    """排除仍有外部父程序的實例；接收安裝目錄內啟動器退出後的孤兒程序。"""
    try:
        current = child
        visited = set()
        for _ in range(32):
            if current.pid in pids:
                return True
            parent_pid = current.ppid()
            if parent_pid in pids:
                return True
            if not parent_pid or parent_pid in visited:
                break
            visited.add(parent_pid)
            try:
                parent = psutil.Process(parent_pid)
            except psutil.NoSuchProcess:
                # pyappify／Python 啟動器可能在第一次輪詢前已退出。
                return True
            if parent_pid not in candidates:
                return False
            current = parent
    except (psutil.Error, OSError):
        pass
    return False


class OkScriptAdapter(Adapter):
    id = "ok"
    display_name = "OK 系列"
    requires_admin = True
    uses_install_dir = True
    task_param = "task"
    task_label = "任務"
    bool_options = [("exit_after", "完成後關閉遊戲與腳本", True)]

    def validate(self, params: dict[str, Any]) -> None:
        p = self.merged(params)
        if not p.get("install_dir"):
            raise AdapterError("缺少 install_dir（安裝資料夾）")
        if not Path(p["install_dir"]).is_dir():
            raise AdapterError(f"安裝資料夾不存在：{p['install_dir']}")
        if not str(p.get("task", "")).strip() or p.get("task") is None:
            raise AdapterError("缺少 task（任務類別名稱）")
        try:
            grace = float(p.get("startup_grace", 60))
            if not math.isfinite(grace) or grace < 0:
                raise ValueError()
        except (TypeError, ValueError):
            raise AdapterError("startup_grace 必須是大於或等於 0 的秒數") from None
        if not isinstance(p.get("exit_after", True), bool):
            raise AdapterError("exit_after 必須是 True 或 False")
        try:
            StallWatch([], p.get("stall_minutes", 20))
        except (TypeError, ValueError):
            raise AdapterError("stall_minutes 必須是有限的分鐘數（0 或負數停用）") from None

    def _layout(self, p: dict[str, Any]) -> tuple[Path, Path, Path]:
        root = Path(p["install_dir"]).resolve()
        if p.get("exe"):
            exe = _child(root, p["exe"])
        else:
            choices = [f for f in root.glob("ok-*.exe") if f.is_file()]
            if len(choices) != 1:
                raise AdapterError("找不到唯一的 ok-*.exe，請指定 exe 啟動器檔名")
            exe = choices[0]
        if not exe.is_file():
            raise AdapterError(f"找不到啟動器：{exe}")
        return root, exe, _app_dir(root, p.get("app"), exe)

    def list_tasks(self, params: dict[str, Any]) -> list[TaskOption]:
        try:
            _, _, app_dir = self._layout(self.merged(params))
            working = app_dir / "working"
            translators = []
            for locale in ("zh_TW", "zh_CN"):
                for path in sorted((working / "i18n" / locale / "LC_MESSAGES").glob("*.mo")):
                    try:
                        with path.open("rb") as stream:
                            translators.append(gettext.GNUTranslations(stream))
                    except (OSError, EOFError, ValueError, struct.error):
                        continue
            options = []
            for module, name in _tasks(app_dir):
                display = _task_name(working, module, name)
                for translator in translators:
                    translated = translator.gettext(display)
                    if translated != display:
                        display = translated
                        break
                options.append(TaskOption(name, f"{display}（{name}）" if display != name else name))
            return options
        except (AdapterError, OSError, ValueError, TypeError, KeyError):
            return []

    def script_version(self, params: dict) -> str | None:
        try:
            root, _, app_dir = self._layout(self.merged(params))
            return read_version(root, app_dir.name)
        except Exception:
            return None

    def run(self, ctx: RunContext, params: dict[str, Any]) -> str:
        self.validate(params)
        p = self.merged(params)
        root, exe, app_dir = self._layout(p)
        index = resolve_task_index(root, app_dir.name, str(p["task"]))
        ctx.check()
        if process.processes_under(root):
            raise AdapterError("此安裝資料夾已有程序執行，請先關閉原本的 OK 腳本再試")
        tail = _LogTail(app_dir / "working" / "logs")
        command = [str(exe), "-t", str(index)]
        if p.get("exit_after", True):
            command.append("-e")
        ctx.log(f"啟動 OK 任務：{p['task']}（目前序號 {index}）")
        # 直接呼叫時也提供截止時間；runner 已設定時沿用它。
        original_deadline = ctx.deadline
        if ctx.deadline is None:
            ctx.deadline = time.monotonic() + 3600
        proc = None
        owned: dict[int, psutil.Process] = {}
        family: set[int] = set()
        try:
            ctx.check()
            try:
                proc = process.launch(command, cwd=root)
            except OSError as e:
                raise AdapterError(f"啟動失敗：{e}。請確認 UAC 提示，或以系統管理員執行二遊全勤君") from e
            family.add(proc.pid)
            watch = StallWatch([app_dir / "working" / "logs"], p.get("stall_minutes", 20))
            grace_end = time.monotonic() + float(p.get("startup_grace", 60))
            seen = runtime_seen = success = False
            failures: list[str] = []
            seen_lines: set[str] = set()
            gone_since = None
            while True:
                ctx.check()
                candidates = process.processes_under(root)
                candidate_pids = {child.pid for child in candidates}
                alive = []
                for child in candidates:
                    if _belongs_to(child, family, candidate_pids):
                        owned[child.pid] = child
                        family.add(child.pid)
                        alive.append(child)
                        runtime_seen = runtime_seen or child.pid != proc.pid
                seen = seen or bool(alive)
                # 先取得程序狀態，再讀取最後一批日誌。
                text = tail.read()
                for line in text.splitlines():
                    # read() 會連同上次的緩衝一起回傳；日誌每行帶時間戳記，以行去重
                    if _FAIL.search(line) and line not in seen_lines:
                        seen_lines.add(line)
                        failures.append(line.strip()[:200])
                        ctx.log(f"OK 日誌出現錯誤（腳本可能會自行處理，繼續等待）：{line.strip()[:120]}")
                success = success or any(word in text.lower() for word in _SUCCESS)
                if not alive:
                    if gone_since is None:
                        gone_since = time.monotonic()
                    # 啟動器先退出時，留時間給提權後的 Python 接手。
                    settled = time.monotonic() - gone_since >= 0.75
                    if seen and settled and (runtime_seen or success or time.monotonic() >= grace_end):
                        code = proc.poll()
                        if not success and failures:
                            raise AdapterError(f"OK 任務失敗：{failures[-1]}")
                        if code not in (None, 0) and not success:
                            raise AdapterError(f"OK 啟動器退出碼 {code}，未確認任務成功")
                        if success:
                            message = "OK 任務完成（日誌確認成功）"
                            if failures:
                                message += f"，期間有 {len(failures)} 則錯誤訊息（例如子任務失敗）"
                        else:
                            message = "OK 程序已結束（無法從日誌確認結果，視為成功）"
                        ctx.log(message)
                        return message
                    if not seen and time.monotonic() >= grace_end:
                        raise AdapterError("寬限期內未偵測到 OK 程序，可能被 UAC 擋下；請確認提示或以系統管理員執行二遊全勤君")
                else:
                    gone_since = None
                idle = watch.check()
                if time.monotonic() >= grace_end and idle is not None and idle >= watch.stall_minutes * 60:
                    raise AdapterError(f"卡死：日誌已 {idle / 60:.1f} 分鐘沒有更新")
                ctx.sleep(0.25)
        finally:
            capture_if_failing(ctx)
            # 啟動前已排除既有實例，清理也涵蓋提權後脫離啟動器的程序。
            if proc is not None:
                if proc.poll() is None:
                    try:
                        process.kill_tree(proc.pid)
                    except psutil.NoSuchProcess:
                        pass
                    except (psutil.AccessDenied, OSError) as e:
                        ctx.log(f"無法關閉啟動器：{e}")
                alive = process.processes_under(root)
                candidate_pids = {child.pid for child in alive}
                # 取消可能發生在子程序剛出現時，補抓有本次父程序的成員。
                for child in alive:
                    if _belongs_to(child, family, candidate_pids):
                        owned[child.pid] = child
                        family.add(child.pid)
                # 清理時才出現、未被本次追蹤的程序不能整批關閉。
                tracked = [child for child in alive if child.pid in owned and owned[child.pid] == child]
                try:
                    if alive and len(tracked) == len(alive):
                        process.kill_under(root)
                except psutil.NoSuchProcess:
                    pass
                except (psutil.AccessDenied, OSError) as e:
                    ctx.log(f"無法關閉 OK 程序，請以系統管理員執行二遊全勤君：{e}")
                for child in tracked:
                    try:
                        if child.is_running():
                            process.kill_tree(child.pid)
                    except psutil.NoSuchProcess:
                        pass
                    except (psutil.AccessDenied, OSError) as e:
                        ctx.log(f"無法關閉 OK 程序 {child.pid}：{e}")
            ctx.deadline = original_deadline

    def detect(self) -> list[Path]:
        deadline = time.monotonic() + 2.5
        found: dict[str, Path] = {}
        roots, desktops = _search_roots()

        def add(exe: Path):
            if exe.name.lower().startswith("ok-") and exe.suffix.lower() == ".exe" and exe.is_file():
                directory = exe.parent.resolve()
                found[os.path.normcase(str(directory))] = directory

        for desktop in desktops:
            if time.monotonic() >= deadline:
                break
            for link in desktop.glob("*.lnk"):
                if time.monotonic() >= deadline:
                    break
                target = _shortcut_target(link)
                if target:
                    add(target)
        for root in roots:
            if time.monotonic() >= deadline:
                break
            try:
                with os.scandir(root) as entries:
                    children = []
                    for entry in entries:
                        if time.monotonic() >= deadline:
                            break
                        if entry.is_file():
                            if entry.name.lower().startswith("ok-") and entry.name.lower().endswith(".exe"):
                                add(Path(entry.path))
                        elif entry.is_dir(follow_symlinks=False):
                            children.append(Path(entry.path))
                for directory in children:
                    if time.monotonic() >= deadline:
                        break
                    try:
                        with os.scandir(directory) as entries:
                            for entry in entries:
                                if time.monotonic() >= deadline:
                                    break
                                if entry.name.lower().startswith("ok-") and entry.name.lower().endswith(".exe") and entry.is_file():
                                    add(Path(entry.path))
                    except OSError:
                        continue
            except OSError:
                continue
        return sorted(found.values(), key=lambda p: str(p).lower())


def _search_roots() -> tuple[list[Path], list[Path]]:
    user = Path(os.environ.get("USERPROFILE", str(Path.home())))
    desktops = [user / "Desktop"]
    if os.environ.get("PUBLIC"):
        desktops.append(Path(os.environ["PUBLIC"]) / "Desktop")
    if os.environ.get("OneDrive"):
        desktops.append(Path(os.environ["OneDrive"]) / "Desktop")
    roots = [*desktops, user / "Downloads", user / "Documents"]
    for key in ("LOCALAPPDATA", "ProgramFiles", "ProgramFiles(x86)"):
        if os.environ.get(key):
            roots.append(Path(os.environ[key]))
    if os.name == "nt":
        mask = ctypes.windll.kernel32.GetLogicalDrives()
        for index in range(26):
            drive = f"{chr(65 + index)}:\\"
            if mask & (1 << index) and ctypes.windll.kernel32.GetDriveTypeW(drive) == 3:
                roots.append(Path(drive))
    return list(dict.fromkeys(roots)), list(dict.fromkeys(desktops))


def _shortcut_target(link: Path) -> Path | None:
    try:
        import comtypes
        from comtypes.client import CreateObject

        # 不呼叫 CoUninitialize：shell 物件在函式返回後才釋放，先反初始化會造成存取違規
        comtypes.CoInitialize()
        # 動態介面不產生寫入使用者目錄的型別快取；僅讀取，不 Save。
        shell = CreateObject("WScript.Shell", dynamic=True)
        target = shell.CreateShortcut(str(link)).TargetPath
        return Path(os.path.expandvars(target)) if target else None
    except Exception:
        return None
