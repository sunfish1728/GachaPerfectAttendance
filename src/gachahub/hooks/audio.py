"""靜音鉤子。"""

from __future__ import annotations

from ctypes import POINTER, cast
from typing import Any

import comtypes
from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

from ..core.context import RunContext
from .base import Hook, register


def _com_init() -> None:
    # 不呼叫 CoUninitialize：COM 物件存於區域變數，會在函式返回後才 Release，
    # 若先反初始化會造成存取違規。工作執行緒結束時 COM 會自行清理。
    comtypes.CoInitialize()


def _endpoint_volume():
    device = AudioUtilities.GetSpeakers()
    # 新版直接提供音量介面；舊版需從 IMMDevice 啟用。
    if hasattr(device, "EndpointVolume"):
        return device.EndpointVolume
    interface = device.Activate(IAudioEndpointVolume._iid_, comtypes.CLSCTX_ALL, None)
    return cast(interface, POINTER(IAudioEndpointVolume))


@register
class MuteHook(Hook):
    """靜音。params: mode: master | sessions；exclude: [程序名, ...]"""

    type = "mute"
    display_name = "靜音"

    def apply(self, ctx: RunContext) -> dict[str, Any] | None:
        mode = self.params.get("mode", "master")
        if mode not in ("master", "sessions"):
            raise ValueError(f"不支援的靜音模式：{mode}")
        _com_init()
        try:
            if mode == "master":
                volume = _endpoint_volume()
                state = {"mode": "master", "mute": bool(volume.GetMute())}
                volume.SetMute(True, None)
                ctx.log("已將系統主音量靜音")
                return state

            sessions = AudioUtilities.GetAllSessions()
            exclude = {name.casefold() for name in self.params.get("exclude", [])}
            state = {"mode": "sessions", "sessions": []}
            for session in sessions:
                try:
                    process = session.Process
                    if process is None:
                        continue
                    name = process.name()
                    if name.casefold() in exclude:
                        continue
                    volume = session.SimpleAudioVolume
                    record = {"pid": int(session.ProcessId), "name": name,
                              "mute": bool(volume.GetMute())}
                    volume.SetMute(True, None)
                    state["sessions"].append(record)
                    ctx.log(f"已將 {name} 靜音")
                except Exception as exc:
                    # 程序可能在列舉後結束，繼續處理其餘工作階段。
                    ctx.log(f"略過無法靜音的音訊工作階段：{exc}")
            return state
        except Exception as exc:
            ctx.log(f"無法取得或靜音音訊裝置：{exc}")
            return None


    def restore(self, ctx: RunContext, state: dict[str, Any] | None) -> None:
        if state is None:
            return
        _com_init()
        try:
            if state["mode"] == "master":
                _endpoint_volume().SetMute(state["mute"], None)
                ctx.log("已還原系統主音量的靜音狀態")
                return

            remaining = list(state["sessions"])
            for session in AudioUtilities.GetAllSessions():
                try:
                    # 同一程序可能有多個工作階段，每筆記錄只使用一次。
                    record = next((item for item in remaining
                                   if item["pid"] == session.ProcessId), None)
                    if record is None:
                        continue
                    session.SimpleAudioVolume.SetMute(record["mute"], None)
                    remaining.remove(record)
                    ctx.log(f"已還原 {record['name']} 的靜音狀態")
                except Exception as exc:
                    ctx.log(f"略過無法還原的音訊工作階段：{exc}")
        except Exception as exc:
            ctx.log(f"無法還原音訊靜音狀態：{exc}")

