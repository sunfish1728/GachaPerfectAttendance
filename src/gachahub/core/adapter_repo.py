"""宣告式 YAML 倉庫：下載、驗證、備份後原子替換，不載入 Python 程式碼。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from .compat import _compare, parse_version
from .registry import AdapterInfo, BUILTIN

MAX_DOWNLOAD = 1024 * 1024


class RepoError(Exception):
    """可直接呈現給使用者的倉庫錯誤。"""


class RemoteAdapter(BaseModel):
    id: str = Field(min_length=1)
    name: str
    version: int = Field(strict=True, ge=0)
    file: str
    sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    min_app: str = ""
    note: str = ""


class RepoIndex(BaseModel):
    # 欄位名 schema 會遮蔽 BaseModel 的同名方法，以別名對應 JSON 的 "schema"
    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)
    schema_version: Literal[1] = Field(1, alias="schema")
    updated: str
    adapters: list[RemoteAdapter]


class UpdateItem(BaseModel):
    id: str
    name: str
    local_version: int | None
    remote_version: int
    action: Literal["new", "update", "same", "skip_app_too_old"]
    note: str


def _download(url: str, timeout: float) -> bytes:
    if urllib.parse.urlsplit(url).scheme not in ("https", "file"):
        raise RepoError("倉庫網址只支援 https:// 或 file://")
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "gachahub-adapter-repo"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read(MAX_DOWNLOAD + 1)
        if len(data) > MAX_DOWNLOAD:
            raise RepoError("下載內容超過 1 MB 限制")
        return data
    except RepoError:
        raise
    except Exception as exc:
        raise RepoError(f"下載倉庫內容失敗：{exc}") from exc


def fetch_index(url: str, timeout=15) -> RepoIndex:
    """下載最多 1 MB 的 UTF-8 索引；失敗以繁中 RepoError 回報。"""
    try:
        data = json.loads(_download(url, timeout).decode("utf-8-sig"))
        if not isinstance(data, dict) or type(data.get("schema")) is not int or data["schema"] != 1:
            raise RepoError("不支援的倉庫索引 schema（必須為 1）")
        index = RepoIndex.model_validate(data)
        if len({a.id for a in index.adapters}) != len(index.adapters):
            raise RepoError("倉庫索引有重複的適配器 id")
        if len({a.file.casefold() for a in index.adapters}) != len(index.adapters):
            raise RepoError("倉庫索引有重複的檔名")
        return index
    except RepoError:
        raise
    except Exception as exc:
        raise RepoError(f"倉庫索引格式錯誤：{exc}") from exc


def plan(index: RepoIndex, local: dict[str, AdapterInfo], app_version: str) -> list[UpdateItem]:
    """只升級較新的定義；無法判斷最低程式版本時保守略過。"""
    current = parse_version(app_version)
    items = []
    for remote in index.adapters:
        info = local.get(remote.id)
        minimum = parse_version(remote.min_app)
        if remote.min_app and (minimum is None or current is None or _compare(current, minimum) < 0):
            action = "skip_app_too_old"
        elif info is None:
            action = "new"
        elif remote.version > info.version:
            action = "update"
        else:
            action = "same"
        items.append(UpdateItem(id=remote.id, name=remote.name,
                                local_version=info.version if info else None,
                                remote_version=remote.version, action=action, note=remote.note))
    return items


def _filename(name: str) -> None:
    # 同時拒絕 Windows 分隔符、磁碟／ADS、URL 跳脫與保留裝置名稱。
    if (not name or re.search(r'[\\/:*?"<>|%#\x00-\x1f]', name)
            or name.endswith((" ", ".")) or Path(name).suffix.lower() not in (".yaml", ".yml")
            or re.fullmatch(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])", name.split(".")[0], re.I)):
        raise RepoError(f"檔名必須是單純的 YAML 檔名：{name}")


def _definition(data: bytes, remote: RemoteAdapter | None = None) -> dict:
    doc = yaml.safe_load(data.decode("utf-8-sig"))
    if not isinstance(doc, dict) or not isinstance(doc.get("id"), str) or not doc["id"]:
        raise RepoError("YAML 必須含有非空的 id")
    if remote is not None and doc["id"] != remote.id:
        raise RepoError("YAML id 與索引不一致")
    if doc.get("base") not in BUILTIN:
        raise RepoError(f"YAML base 不存在：{doc.get('base')}")
    version = doc.get("version", 0)
    if type(version) is not int or version < 0:
        raise RepoError("YAML version 必須是非負整數")
    if remote is not None and version != remote.version:
        raise RepoError("YAML version 與索引不一致")
    for key in ("defaults", "compat"):
        if key in doc and not isinstance(doc[key], dict):
            raise RepoError(f"YAML {key} 必須是字典")
    if "fields" in doc and (not isinstance(doc["fields"], list)
                            or any(not isinstance(f, dict) for f in doc["fields"])):
        raise RepoError("YAML fields 必須是字典清單")
    if "name" in doc and not isinstance(doc["name"], str):
        raise RepoError("YAML name 必須是文字")
    compat = doc.get("compat", {})
    for key in ("min", "max"):
        if key in compat and parse_version(compat[key]) is None:
            raise RepoError(f"YAML compat.{key} 不是有效版本")
    if compat and compat.get("on_mismatch", "warn") not in ("warn", "block"):
        raise RepoError("YAML on_mismatch 必須是 warn 或 block")
    return doc


def _atomic_write(path: Path, data: bytes) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".adapter-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def apply(index_url: str, index: RepoIndex, items: list[UpdateItem],
          adapters_dir: Path, timeout=15) -> list[str]:
    """逐項處理 new/update；任何單項失敗都保留原檔並繼續下一項。

    同 id 本機檔案若使用不同檔名，沿用原檔名，避免重載時舊定義覆蓋新定義。
    呼叫者須先以 plan 檢查 min_app；寫入後重新建立／載入登錄表即可使用。
    """
    results = []
    root = Path(adapters_dir).resolve()
    backup = root / ".backup" / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    for item in items:
        if item.action not in ("new", "update"):
            continue
        try:
            matches = [a for a in index.adapters if a.id == item.id]
            if len(matches) != 1:
                raise RepoError("索引缺少適配器或 id 重複")
            remote = matches[0]
            _filename(remote.file)
            data = _download(urllib.parse.urljoin(index_url, remote.file), timeout)
            if hashlib.sha256(data).hexdigest() != remote.sha256.lower():
                raise RepoError("SHA256 驗證不符")
            _definition(data, remote)
            root.mkdir(parents=True, exist_ok=True)
            target = root / remote.file
            existing = []
            for path in root.glob("*.y*ml"):
                try:
                    doc = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
                    if isinstance(doc, dict) and doc.get("id") == remote.id:
                        existing.append(path)
                except (OSError, ValueError, yaml.YAMLError):
                    continue
            if len(existing) > 1:
                raise RepoError("本機有重複 id，請先整理適配器檔案")
            if existing:
                target = existing[0]
            elif target.exists():
                raise RepoError("同名檔案屬於其他適配器，略過以免覆蓋")
            if target.is_symlink() or not target.resolve().is_relative_to(root):
                raise RepoError("目標路徑超出適配器資料夾或為符號連結")
            if target.exists():
                if not backup.resolve().is_relative_to(root):
                    raise RepoError("備份路徑超出適配器資料夾")
                backup.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, backup / target.name)
            _atomic_write(target, data)
            results.append(f"{item.name}：更新完成")
        except Exception as exc:
            results.append(f"{item.name}：略過，{exc}")
    return results


def build_index(adapters_dir: Path, out_dir: Path) -> Path:
    """複製本機 YAML 並產生 schema 1 索引；不發布到網路。"""
    try:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        entries = []
        for path in sorted(Path(adapters_dir).glob("*.y*ml")):
            _filename(path.name)
            data = path.read_bytes()
            doc = _definition(data)
            if any(entry.id == doc["id"] for entry in entries):
                raise RepoError(f"本機有重複 id：{doc['id']}")
            entries.append(RemoteAdapter(id=doc["id"], name=doc.get("name", doc["id"]),
                                        version=doc.get("version", 0), file=path.name,
                                        sha256=hashlib.sha256(data).hexdigest(),
                                        min_app=doc.get("min_app", ""),
                                        note=doc.get("compat", {}).get("note", "")))
            _atomic_write(out_dir / path.name, data)
        index = RepoIndex(updated=datetime.now().date().isoformat(), adapters=entries)
        output = out_dir / "index.json"
        _atomic_write(output, json.dumps(index.model_dump(), ensure_ascii=False, indent=2).encode("utf-8"))
        return output
    except RepoError:
        raise
    except Exception as exc:
        raise RepoError(f"建立適配器倉庫失敗：{exc}") from exc
