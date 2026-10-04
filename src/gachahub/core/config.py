"""設定存取。可攜式：所有資料都放在程式根目錄下，不寫入使用者目錄或登錄檔。

  data/chains/*.yaml   任務鏈
  adapters/*.yaml      宣告式適配器
  runtime/             執行期暫存（標記檔、日誌）
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import yaml

from .models import TaskChain


def app_root() -> Path:
    env = os.environ.get("GACHAHUB_ROOT")
    if env:
        return Path(env)
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parents[3]


class Paths:
    def __init__(self, root: Path | None = None):
        self.root = root or app_root()
        self.chains = self.root / "data" / "chains"
        self.adapters = self.root / "adapters"
        self.runtime = self.root / "runtime"
        self.logs = self.runtime / "logs"

    def ensure(self) -> None:
        for d in (self.chains, self.adapters, self.runtime, self.logs):
            d.mkdir(parents=True, exist_ok=True)


def _safe_filename(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', "_", name).strip() or "chain"


class ChainStore:
    def __init__(self, paths: Paths):
        self.dir = paths.chains

    def list(self) -> list[TaskChain]:
        chains = []
        for path in sorted(self.dir.glob("*.yaml")):
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            chains.append(TaskChain.model_validate(data))
        return chains

    def path_for(self, name: str) -> Path:
        return self.dir / f"{_safe_filename(name)}.yaml"

    def save(self, chain: TaskChain) -> Path:
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.path_for(chain.name)
        tmp = path.with_suffix(".yaml.tmp")
        tmp.write_text(
            yaml.safe_dump(chain.model_dump(mode="json"), allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        tmp.replace(path)  # 原子寫入，避免斷電時設定檔損毀
        return path

    def delete(self, name: str) -> None:
        self.path_for(name).unlink(missing_ok=True)
