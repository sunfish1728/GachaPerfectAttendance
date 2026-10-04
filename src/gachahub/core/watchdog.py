"""以日誌大小與修改時間偵測長時間沒有進展。"""

from __future__ import annotations

import math
import time
from pathlib import Path


class StallWatch:
    def __init__(self, paths: list[Path], stall_minutes: float):
        self.paths = [Path(p) for p in paths]
        self.stall_minutes = float(stall_minutes)
        if not math.isfinite(self.stall_minutes):
            raise ValueError("stall_minutes 必須是有限的分鐘數")
        self._state = self._scan() if self.stall_minutes > 0 else {}
        self._changed_at = time.monotonic()

    def _scan(self) -> dict[Path, tuple[int, int]]:
        state = {}
        for root in self.paths:
            try:
                paths = root.rglob("*") if root.is_dir() else [root]
                for path in paths:
                    try:
                        if path.is_file():
                            stat = path.stat()
                            state[path] = (stat.st_size, stat.st_mtime_ns)
                    except OSError:
                        pass
            except OSError:
                pass
        return state

    def check(self, now: float | None = None) -> float | None:
        """now 為 monotonic 秒數；停用時回傳 None，變化時重設計時。"""
        if self.stall_minutes <= 0:
            return None
        now = time.monotonic() if now is None else now
        state = self._scan()
        if state != self._state:
            self._state, self._changed_at = state, now
        return max(0.0, now - self._changed_at)

    def idle_for(self) -> float:
        return self.check() or 0.0

    def stalled(self) -> bool:
        idle = self.check()
        return idle is not None and idle >= self.stall_minutes * 60
