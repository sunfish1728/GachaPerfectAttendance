import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from gachahub.core.context import RunContext
from gachahub.hooks import audio
from gachahub.hooks.base import HOOKS


class FakeVolume:
    def __init__(self, mute=False):
        self.mute = mute
        self.calls = []

    def GetMute(self):
        return self.mute

    def SetMute(self, mute, context):
        self.mute = bool(mute)
        self.calls.append(bool(mute))


def session(pid, name, mute=False):
    return SimpleNamespace(
        ProcessId=pid,
        Process=SimpleNamespace(name=lambda: name) if name else None,
        SimpleAudioVolume=FakeVolume(mute),
    )


@pytest.fixture
def env(monkeypatch):
    logs = []
    ctx = RunContext(work_dir=Path.cwd() / "runtime", on_log=logs.append)
    data = SimpleNamespace(ctx=ctx, logs=logs, volume=FakeVolume(), sessions=[],
                           com_calls=[], active=False, error=None)

    def initialize():
        data.active = True
        data.com_calls.append("init")

    def speakers():
        assert data.active
        if data.error:
            raise data.error
        return SimpleNamespace(EndpointVolume=data.volume)

    def sessions():
        assert data.active
        if data.error:
            raise data.error
        return data.sessions

    monkeypatch.setattr(audio.comtypes, "CoInitialize", initialize)
    monkeypatch.setattr(audio, "AudioUtilities", SimpleNamespace(
        GetSpeakers=speakers, GetAllSessions=sessions,
    ))
    return data


def test_registered():
    assert HOOKS["mute"] is audio.MuteHook
    assert audio.MuteHook.display_name == "靜音"


@pytest.mark.parametrize("original", [False, True])
def test_master_roundtrip(env, original):
    env.volume.mute = original
    hook = audio.MuteHook()
    state = hook.apply(env.ctx)
    assert state == {"mode": "master", "mute": original}
    assert env.volume.mute is True
    # 還原依據保存的狀態，不受後來修改參數影響。
    hook.params["mode"] = "sessions"
    hook.restore(env.ctx, json.loads(json.dumps(state)))
    assert env.volume.mute is original
    assert env.volume.calls == [True, original]
    assert env.com_calls == ["init", "init"]
    assert env.logs


def test_legacy_device(env, monkeypatch):
    activated = []
    pointer = object()

    def activate(iid, context, reserved):
        assert env.active
        activated.append((iid, context, reserved))
        return pointer

    def fake_cast(interface, pointer_type):
        assert interface is pointer
        return env.volume

    monkeypatch.setattr(audio.AudioUtilities, "GetSpeakers",
                        lambda: SimpleNamespace(Activate=activate))
    monkeypatch.setattr(audio, "cast", fake_cast)
    hook = audio.MuteHook()
    state = hook.apply(env.ctx)
    hook.restore(env.ctx, state)
    assert env.volume.mute is False
    assert activated == [(audio.IAudioEndpointVolume._iid_, audio.comtypes.CLSCTX_ALL, None)] * 2


def test_sessions_exclude_and_restore(env):
    game = session(1, "game.exe")
    muted = session(2, "music.exe", True)
    excluded = session(3, "Discord.EXE")
    system = session(0, None)
    env.sessions = [game, muted, excluded, system]
    hook = audio.MuteHook({"mode": "sessions", "exclude": ["discord.exe"]})
    state = hook.apply(env.ctx)
    assert state == {"mode": "sessions", "sessions": [
        {"pid": 1, "name": "game.exe", "mute": False},
        {"pid": 2, "name": "music.exe", "mute": True},
    ]}
    assert game.SimpleAudioVolume.mute is True
    assert muted.SimpleAudioVolume.mute is True
    assert excluded.SimpleAudioVolume.calls == []
    assert system.SimpleAudioVolume.calls == []

    # 重新列舉得到新物件，仍應能依 pid 找回；新出現的程序不還原。
    game_now = session(1, "game.exe", True)
    muted_now = session(2, "music.exe", True)
    new = session(4, "new.exe", True)
    env.sessions = [muted_now, new, game_now, excluded, system]
    hook.restore(env.ctx, json.loads(json.dumps(state)))
    assert game_now.SimpleAudioVolume.mute is False
    assert muted_now.SimpleAudioVolume.mute is True
    assert new.SimpleAudioVolume.calls == []
    assert excluded.SimpleAudioVolume.calls == []
    assert system.SimpleAudioVolume.calls == []
    assert env.com_calls == ["init", "init"]


def test_missing_session_is_skipped(env):
    env.sessions = [session(1, "closed.exe"), session(2, "alive.exe")]
    hook = audio.MuteHook({"mode": "sessions"})
    state = hook.apply(env.ctx)
    alive = env.sessions[1]
    env.sessions = [alive]
    hook.restore(env.ctx, state)
    assert alive.SimpleAudioVolume.mute is False


def test_multiple_sessions_for_one_pid(env):
    env.sessions = [session(1, "game.exe"), session(1, "game.exe", True)]
    hook = audio.MuteHook({"mode": "sessions"})
    state = hook.apply(env.ctx)
    hook.restore(env.ctx, state)
    assert [item.SimpleAudioVolume.mute for item in env.sessions] == [False, True]


def test_restore_none(env):
    audio.MuteHook().restore(env.ctx, None)
    assert env.com_calls == []
    assert env.volume.calls == []


@pytest.mark.parametrize("mode", ["master", "sessions"])
def test_device_failure(env, mode):
    env.error = RuntimeError("沒有音訊裝置")
    assert audio.MuteHook({"mode": mode}).apply(env.ctx) is None
    assert env.com_calls == ["init"]
    assert any("沒有音訊裝置" in message for message in env.logs)


@pytest.mark.parametrize("mode", ["master", "sessions"])
def test_restore_device_failure(env, mode):
    hook = audio.MuteHook({"mode": mode})
    state = hook.apply(env.ctx)
    env.error = RuntimeError("沒有音訊裝置")
    hook.restore(env.ctx, state)
    assert env.com_calls == ["init", "init"]
    assert any("沒有音訊裝置" in message for message in env.logs)


def test_ended_session_during_restore(env):
    alive = session(2, "alive.exe")
    env.sessions = [session(1, "closed.exe"), alive]
    hook = audio.MuteHook({"mode": "sessions"})
    state = hook.apply(env.ctx)

    class EndedSession:
        @property
        def ProcessId(self):
            raise RuntimeError("程序已結束")

    env.sessions = [EndedSession(), alive]
    hook.restore(env.ctx, state)
    assert alive.SimpleAudioVolume.mute is False
