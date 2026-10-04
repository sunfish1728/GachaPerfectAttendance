"""腳本版本相容檢查；只讀版本，不執行腳本。"""

from __future__ import annotations

import re
from itertools import zip_longest

from pydantic import BaseModel

from .adapter import Adapter
from .registry import AdapterInfo


def parse_version(text: str) -> tuple[int, ...] | None:
    """接受 v 前綴與 -beta 等後綴；無數字版本時回傳 None。"""
    if not isinstance(text, str):
        return None
    match = re.fullmatch(r"[vV]?(\d+(?:\.\d+)*)(?:[-+][\w.-]+)?", text.strip())
    if not match:
        return None
    try:
        return tuple(int(part) for part in match[1].split("."))
    except ValueError:
        return None


def _compare(left: tuple[int, ...], right: tuple[int, ...]) -> int:
    """未寫出的尾端補零，例如 2.0 與 2.0.0 相等。"""
    for a, b in zip_longest(left, right, fillvalue=0):
        if a != b:
            return 1 if a > b else -1
    return 0


class CompatResult(BaseModel):
    ok: bool
    version: str | None
    message: str
    block: bool


def check(info: AdapterInfo, adapter: Adapter, params: dict) -> CompatResult:
    """min 含下限，缺位補零；max 含上限，只比較寫出的位數。

    例如 max v1.4 接受所有 1.4.x；max v1.4.6 接受 1.4.6.x。
    沒有宣告或讀不到／無法解析版本時繼續執行。
    """
    if not info.compat:
        return CompatResult(ok=True, version=None, message="", block=False)
    try:
        version = adapter.script_version(params)
    except Exception:
        version = None
    parsed = parse_version(version)
    if parsed is None:
        return CompatResult(ok=True, version=version, message=f"{info.name} 無法讀取版本", block=False)
    lower = parse_version(info.compat.get("min"))
    upper = parse_version(info.compat.get("max"))
    mismatch = ((lower is not None and _compare(parsed, lower) < 0)
                or (upper is not None and _compare(parsed[:len(upper)], upper) > 0))
    message = ""
    if mismatch:
        message = (f"{info.name} 版本 {version} 超出已驗證範圍 "
                   f"{info.compat.get('min') or '不限'} ~ {info.compat.get('max') or '不限'}，腳本可能已改版")
        if info.compat.get("note"):
            message += f"（{info.compat['note']}）"
    return CompatResult(ok=not mismatch, version=version, message=message,
                        block=mismatch and info.compat.get("on_mismatch") == "block")
