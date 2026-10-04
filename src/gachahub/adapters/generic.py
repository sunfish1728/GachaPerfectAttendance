"""通用 exe/bat/ps1 適配器。其他系列可用宣告式 YAML 以它為基底。

參數（皆可由 YAML defaults 提供，步驟 params 覆蓋）：
  command          執行檔路徑（必填）；.bat/.cmd 經 cmd /c，.ps1 經 powershell -File
  args             參數列表，支援 {work_dir} 佔位符
  cwd              工作目錄，預設為執行檔所在目錄
  hide_window      是否隱藏主控台視窗
  completion       完成判斷：exit（預設）| process_gone | marker_file | log_keyword
  process_names    completion=process_gone 時，等待這些程序全部消失
  startup_grace    process_gone 開始判斷前的寬限秒數（等待被監控程序出現）
  marker_file      completion=marker_file 時的標記檔路徑
  log_file         completion=log_keyword 時監看的日誌檔
  success_keywords / fail_keywords   日誌關鍵字
  log_encoding     日誌編碼，預設 utf-8
  stall_minutes    log_keyword 日誌卡死分鐘數，預設 0 停用；startup_grace 內不判定
  success_exit_codes  completion=exit 時視為成功的退出碼，預設 [0]
  kill_on_finish   結束後要關閉的程序名稱（例如遊戲本體）
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ..core import process
from ..core.adapter import Adapter, AdapterError
from ..core.context import RunContext, capture_if_failing
from ..core.watchdog import StallWatch

COMPLETIONS = ("exit", "process_gone", "marker_file", "log_keyword")


def _fmt(value: Any, ctx: RunContext) -> Any:
    if isinstance(value, str):
        return value.replace("{work_dir}", str(ctx.work_dir))
    return value


class GenericAdapter(Adapter):
    id = "generic"
    display_name = "通用程式"

    def validate(self, params: dict[str, Any]) -> None:
        p = self.merged(params)
        if not p.get("command"):
            raise AdapterError("缺少 command")
        if p.get("completion", "exit") not in COMPLETIONS:
            raise AdapterError(f"未知的 completion：{p.get('completion')}")
        c = p.get("completion", "exit")
        if c == "process_gone" and not p.get("process_names"):
            raise AdapterError("completion=process_gone 需要 process_names")
        if c == "marker_file" and not p.get("marker_file"):
            raise AdapterError("completion=marker_file 需要 marker_file")
        if c == "log_keyword" and not (p.get("log_file") and (p.get("success_keywords") or p.get("fail_keywords"))):
            raise AdapterError("completion=log_keyword 需要 log_file 與關鍵字")
        try:
            StallWatch([], p.get("stall_minutes", 0))
        except (TypeError, ValueError):
            raise AdapterError("stall_minutes 必須是有限的分鐘數（0 或負數停用）") from None

    def build_command(self, ctx: RunContext, p: dict[str, Any]) -> list[str]:
        exe = _fmt(p["command"], ctx)
        args = [str(_fmt(a, ctx)) for a in p.get("args", [])]
        suffix = Path(exe).suffix.lower()
        if suffix in (".bat", ".cmd"):
            return ["cmd.exe", "/c", exe, *args]
        if suffix == ".ps1":
            return ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", exe, *args]
        return [exe, *args]

    def run(self, ctx: RunContext, params: dict[str, Any]) -> str:
        p = self.merged(params)
        self.validate(params)
        exe = Path(_fmt(p["command"], ctx))
        cwd = _fmt(p.get("cwd"), ctx) or (exe.parent if exe.parent != Path(".") else None)
        completion = p.get("completion", "exit")

        marker = Path(_fmt(p["marker_file"], ctx)) if completion == "marker_file" else None
        if marker and marker.exists():
            marker.unlink()
        log_path = Path(_fmt(p["log_file"], ctx)) if completion == "log_keyword" else None
        log_offset = log_path.stat().st_size if log_path and log_path.exists() else 0

        cmd = self.build_command(ctx, p)
        ctx.log(f"啟動：{' '.join(cmd)}")
        proc = process.launch(cmd, cwd=cwd, hide_window=bool(p.get("hide_window")))
        try:
            if completion == "exit":
                code = process.wait_exit(ctx, proc)
                ok_codes = p.get("success_exit_codes", [0])
                if code not in ok_codes:
                    raise AdapterError(f"退出碼 {code}")
                return f"完成（退出碼 {code}）"
            if completion == "process_gone":
                return self._wait_process_gone(ctx, p)
            if completion == "marker_file":
                return self._wait_marker(ctx, marker)
            return self._wait_log(ctx, p, log_path, log_offset, proc)
        finally:
            capture_if_failing(ctx)
            if proc.poll() is None:
                process.kill_tree(proc.pid)

    def cleanup(self, ctx: RunContext, params: dict[str, Any]) -> None:
        for name in self.merged(params).get("kill_on_finish", []) or []:
            n = process.kill_by_name(name)
            if n:
                ctx.log(f"已關閉 {name}（{n} 個）")

    # --- 完成判斷 ---

    def _wait_process_gone(self, ctx: RunContext, p: dict[str, Any]) -> str:
        names = p["process_names"]
        grace_end = time.monotonic() + float(p.get("startup_grace", 30))
        seen = False
        while True:
            alive = any(process.find_processes(n) for n in names)
            if alive:
                seen = True
            elif seen or time.monotonic() >= grace_end:
                if not seen:
                    raise AdapterError(f"寬限期內未偵測到程序：{', '.join(names)}")
                return "監控程序已結束"
            ctx.sleep(2.0)

    def _wait_marker(self, ctx: RunContext, marker: Path) -> str:
        while not marker.exists():
            ctx.sleep(1.0)
        content = marker.read_text(encoding="utf-8", errors="replace").strip()
        return f"標記檔出現 {content}".strip()

    def _wait_log(self, ctx: RunContext, p: dict[str, Any], log_path: Path, offset: int, proc) -> str:
        enc = p.get("log_encoding", "utf-8")
        ok_kw = p.get("success_keywords", []) or []
        bad_kw = p.get("fail_keywords", []) or []
        buf = ""
        watch = StallWatch([log_path], p.get("stall_minutes", 0))
        grace_end = time.monotonic() + float(p.get("startup_grace", 30))
        while True:
            # 先記錄程序狀態再讀日誌，避免程序結束前最後寫入的關鍵字被漏讀
            exited = proc.poll() is not None
            if log_path.exists():
                size = log_path.stat().st_size
                if size < offset:  # 日誌被輪替
                    offset = 0
                if size > offset:
                    with log_path.open("rb") as f:
                        f.seek(offset)
                        data = f.read()
                    offset += len(data)
                    buf = (buf + data.decode(enc, errors="replace"))[-65536:]
                    for kw in bad_kw:
                        if kw in buf:
                            raise AdapterError(f"日誌出現失敗關鍵字：{kw}")
                    for kw in ok_kw:
                        if kw in buf:
                            return f"日誌出現成功關鍵字：{kw}"
            if exited and not p.get("log_outlives_process", False):
                raise AdapterError(f"程序已結束（退出碼 {proc.returncode}）但日誌未出現成功關鍵字")
            idle = watch.check()
            if time.monotonic() >= grace_end and idle is not None and idle >= watch.stall_minutes * 60:
                raise AdapterError(f"卡死：日誌已 {idle / 60:.1f} 分鐘沒有更新")
            ctx.sleep(1.0)
