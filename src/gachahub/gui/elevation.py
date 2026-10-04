"""以系統管理員身分重新啟動本程式。

OK 系列與一條龍都需要管理員權限：本程式未提權時，每次啟動腳本都會跳出 UAC，
且無法監控、終止腳本程序。提權後啟動的子程序會直接繼承權限，不再詢問。
"""

from __future__ import annotations

import ctypes
import sys
from pathlib import Path

from ..core.process import is_admin  # noqa: F401  供 GUI 匯入


def _pythonw() -> str:
    exe = Path(sys.executable)
    w = exe.with_name("pythonw.exe")
    return str(w if w.exists() else exe)


def relaunch_as_admin(extra_args: list[str] | None = None) -> bool:
    """以 UAC 提權啟動新實例。回傳 True 表示已送出（使用者同意），呼叫端應自行結束。
    注意：提權後的程序不會繼承目前的環境變數；安裝版靠 python/Lib/site-packages/gachahub.pth、開發環境靠 editable 安裝找到程式。"""
    args = ["-m", "gachahub", "--elevated", *(extra_args or [])]
    params = " ".join(f'"{a}"' if " " in a else a for a in args)
    root = Path(__file__).resolve().parents[3]
    # ShellExecuteW 回傳值 > 32 代表成功；使用者拒絕 UAC 時回傳 5（存取被拒）
    rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", _pythonw(), params, str(root), 1)
    return rc > 32
