"""適配器登錄表：內建 Python 適配器 + adapters/ 目錄下的宣告式 YAML 適配器。

YAML 格式：
  id: ok-nte
  name: ok 異環
  base: generic          # 以哪個內建適配器為基底
  defaults: {...}        # 預設參數，步驟 params 可覆蓋
  fields: [...]          # 可選：GUI 需要使用者填寫的欄位說明
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..adapters.generic import GenericAdapter
from ..adapters.ok_script import OkScriptAdapter
from ..adapters.onedragon import OneDragonAdapter
from .adapter import Adapter, AdapterError

BUILTIN: dict[str, type[Adapter]] = {
    GenericAdapter.id: GenericAdapter,
    OkScriptAdapter.id: OkScriptAdapter,
    OneDragonAdapter.id: OneDragonAdapter,
}


@dataclass
class AdapterInfo:
    id: str
    name: str
    base: str
    defaults: dict[str, Any] = field(default_factory=dict)
    fields: list[dict[str, Any]] = field(default_factory=list)
    source: Path | None = None
    version: int = 0
    compat: dict = field(default_factory=dict)


class AdapterRegistry:
    def __init__(self) -> None:
        self.infos: dict[str, AdapterInfo] = {
            cls.id: AdapterInfo(id=cls.id, name=cls.display_name or cls.id, base=cls.id)
            for cls in BUILTIN.values()
        }
        self.errors: list[str] = []

    def load_dir(self, directory: Path) -> None:
        if not directory.is_dir():
            return
        for path in sorted(directory.glob("*.y*ml")):
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                info = AdapterInfo(
                    id=data["id"],
                    name=data.get("name", data["id"]),
                    base=data.get("base", "generic"),
                    defaults=data.get("defaults") or {},
                    fields=data.get("fields") or [],
                    source=path,
                    version=data.get("version", 0),
                    compat=data.get("compat") or {},
                )
                if type(info.version) is not int or not isinstance(info.compat, dict):
                    raise AdapterError("version 必須是整數，compat 必須是字典")
                if info.base not in BUILTIN:
                    raise AdapterError(f"未知的 base：{info.base}")
                self.infos[info.id] = info
            except Exception as e:  # 單一壞檔不影響其他適配器
                self.errors.append(f"{path.name}: {e}")

    def create(self, adapter_id: str) -> Adapter:
        info = self.infos.get(adapter_id)
        if info is None:
            raise AdapterError(f"找不到適配器：{adapter_id}")
        return BUILTIN[info.base](defaults=info.defaults)
