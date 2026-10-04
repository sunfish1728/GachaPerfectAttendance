"""內建鉤子。"""

from __future__ import annotations

import subprocess
from typing import Any

from ..core import process
from ..core.context import RunContext
from .base import Hook, register

from . import audio  # noqa: F401,E402  註冊靜音鉤子


@register
class KillProcessesHook(Hook):
    """關閉指定程序。params: names: [xxx.exe, ...]"""

    type = "kill_processes"
    display_name = "關閉程式"

    def apply(self, ctx: RunContext) -> Any:
        for name in self.params.get("names", []):
            n = process.kill_by_name(name)
            if n:
                ctx.log(f"已關閉 {name}（{n} 個）")
        return None


@register
class PowerActionHook(Hook):
    """結束後電源動作。params: action: shutdown | sleep | hibernate | none；delay: 秒（關機倒數，可在系統中取消）"""

    type = "power"
    display_name = "電源動作"

    def apply(self, ctx: RunContext) -> Any:
        action = self.params.get("action", "none")
        delay = int(self.params.get("delay", 60))
        if action == "shutdown":
            ctx.log(f"{delay} 秒後關機（可執行 shutdown /a 取消）")
            subprocess.run(["shutdown", "/s", "/t", str(delay)], check=False)
        elif action in ("sleep", "hibernate"):
            ctx.sleep(delay)
            ctx.log("進入睡眠" if action == "sleep" else "進入休眠")
            import ctypes

            # SetSuspendState(hibernate, force, wakeupEventsDisabled)；保留喚醒事件以便排程喚醒
            ctypes.windll.powrprof.SetSuspendState(action == "hibernate", False, False)
        return None
