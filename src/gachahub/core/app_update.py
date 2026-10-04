"""查詢本程式 GitHub Release；只回報資訊，不下載或安裝。"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

from pydantic import BaseModel

from .compat import _compare, parse_version


class UpdateInfo(BaseModel):
    available: bool
    current: str
    latest: str | None = None
    url: str | None = None
    notes: str = ""
    error: str | None = None


def check_github(repo: str, current: str, timeout=10) -> UpdateInfo:
    result = UpdateInfo(available=False, current=current)
    if not isinstance(repo, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo) or any(
            part in (".", "..") for part in repo.split("/")):
        result.error = "請填寫有效的 GitHub 倉庫名稱（owner/name）"
        return result
    try:
        request = urllib.request.Request(
            f"https://api.github.com/repos/{repo}/releases/latest",
            headers={"User-Agent": "gachahub-update-check", "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read(1024 * 1024 + 1)
        if len(data) > 1024 * 1024:
            raise ValueError("發布資訊超過 1 MB 限制")
        release = json.loads(data.decode("utf-8"))
        tag = release.get("tag_name")
        url = release.get("html_url")
        notes = release.get("body")
        notes = "" if notes is None else notes
        if (not isinstance(tag, str) or not isinstance(notes, str)
                or (url is not None and not isinstance(url, str))):
            raise ValueError("發布資訊欄位格式錯誤")
        result.latest, result.url, result.notes = tag, url, notes
        latest, installed = parse_version(tag), parse_version(current)
        if latest is None or installed is None:
            raise ValueError("無法解析目前版本或發布版本")
        result.available = _compare(latest, installed) > 0
    except urllib.error.HTTPError as exc:
        result.error = "此倉庫尚無發布版本" if exc.code == 404 else f"檢查更新失敗：HTTP {exc.code}"
    except Exception as exc:
        result.error = f"檢查更新失敗：{exc}"
    return result
