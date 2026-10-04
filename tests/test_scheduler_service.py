"""排程服務整合測試（無畫面）：到期 → 在場延後 → 倒數 → 執行 → 記錄觸發。"""

import os
import sys
import time
from datetime import datetime, timedelta

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication  # noqa: E402

from gachahub.core.config import Paths  # noqa: E402
from gachahub.core.models import TaskChain, TaskStep  # noqa: E402
from gachahub.core.schedule import Schedule, ScheduleKind  # noqa: E402
from gachahub.gui import scheduler_service as svc_mod  # noqa: E402
from gachahub.gui.controller import AppController  # noqa: E402
from gachahub.gui.settings_page import cfg  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def pump(app, cond, timeout=15.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def env(app, tmp_path, monkeypatch):
    paths = Paths(tmp_path)
    paths.ensure()
    controller = AppController(paths)
    controller.save_chain(TaskChain(name="日常", steps=[
        TaskStep(name="s", adapter="generic", params={"command": sys.executable, "args": ["-c", "pass"], "hide_window": True}),
    ]))
    service = svc_mod.SchedulerService(controller)
    # 排程 5 分鐘前到期；把 first_seen 設在更早，模擬「程式一直開著」
    planned = (datetime.now() - timedelta(minutes=5)).replace(second=0, microsecond=0)
    s = Schedule(chain="日常", kind=ScheduleKind.DAILY, time=planned.strftime("%H:%M"), catch_up_minutes=60)
    service.save(s)
    service.store._state[s.id] = {"first_seen": (planned - timedelta(hours=1)).isoformat()}
    monkeypatch.setattr(cfg.countdownSeconds, "value", 0)  # 不倒數，直接開始
    monkeypatch.setattr(cfg.presenceMaxDefers, "value", 1)
    return app, controller, service, s, planned


def test_due_defer_then_run(env, monkeypatch):
    app, controller, service, s, planned = env
    calls = iter([(True, "2 分鐘內有操作"), (False, "")])
    monkeypatch.setattr(svc_mod.presence, "check", lambda policy: next(calls))
    reports = []
    controller.runFinished.connect(reports.append)

    service.tick()  # 第一次：使用者在場 → 延後
    assert len(service.queue) == 1 and service.queue[0].defers == 1
    assert not controller.running
    service.queue[0].not_before = datetime.now() - timedelta(seconds=1)  # 模擬延後時間已到
    service.tick()  # 第二次：不在場 → 開始
    assert pump(app, lambda: reports)
    assert reports[0].ok
    assert service.store.last_fired(s.id) == planned
    assert not service.queue
    service.tick()  # 已觸發過，不會重複
    assert not service.queue and not controller.running


def test_pause_today_skips_and_marks(env, monkeypatch):
    app, controller, service, s, planned = env
    monkeypatch.setattr(svc_mod.presence, "check", lambda policy: (False, ""))
    service.pause_today()
    service.tick()
    assert not service.queue and not controller.running
    assert service.store.last_fired(s.id) == planned


def test_countdown_postpone_and_skip(env, monkeypatch):
    app, controller, service, s, planned = env
    monkeypatch.setattr(svc_mod.presence, "check", lambda policy: (False, ""))
    monkeypatch.setattr(cfg.countdownSeconds, "value", 60)
    asked = []
    service.countdownRequested.connect(asked.append)
    service.tick()
    assert asked and service.counting is not None
    service.postpone(15)
    assert service.counting is None and service.queue[0].not_before > datetime.now() + timedelta(minutes=14)
    service.queue[0].not_before = datetime.now() - timedelta(seconds=1)
    service.tick()
    assert service.counting is not None
    service.skip()
    assert not service.queue and service.store.last_fired(s.id) == planned and not controller.running
