import sys
import threading
import time
from pathlib import Path

import pytest

from gachahub.core.context import RunContext
from gachahub.core.models import FailPolicy, HookSpec, StepStatus, TaskChain, TaskStep
from gachahub.core.registry import AdapterRegistry
from gachahub.core.runner import ChainRunner
from gachahub.hooks.base import HOOKS, Hook

PY = sys.executable


def py_step(name, code, **kw):
    return TaskStep(name=name, adapter="generic", params={"command": PY, "args": ["-c", code], "hide_window": True}, **kw)


@pytest.fixture
def ctx(tmp_path):
    return RunContext(work_dir=tmp_path / "work")


@pytest.fixture
def runner():
    r = ChainRunner(AdapterRegistry())
    r.retry_delay = 0.0
    return r


class RecordHook(Hook):
    type = "test_record"
    events: list = []

    def apply(self, ctx):
        RecordHook.events.append(("apply", self.params["tag"]))
        return self.params["tag"]

    def restore(self, ctx, state):
        RecordHook.events.append(("restore", state))


@pytest.fixture(autouse=True)
def record_hook():
    HOOKS[RecordHook.type] = RecordHook
    RecordHook.events = []
    yield
    HOOKS.pop(RecordHook.type, None)


def test_success_and_exit_code_failure(runner, ctx):
    chain = TaskChain(name="t", steps=[py_step("ok", "pass"), py_step("bad", "raise SystemExit(3)")])
    rep = runner.run(chain, ctx)
    assert [r.status for r in rep.results] == [StepStatus.SUCCESS, StepStatus.FAILED]
    assert "3" in rep.results[1].message
    assert not rep.ok


def test_retry_then_skip_continues(runner, ctx, tmp_path):
    counter = tmp_path / "n.txt"
    code = (
        "import pathlib,sys;p=pathlib.Path(sys.argv[1]);"
        "n=int(p.read_text() if p.exists() else 0)+1;p.write_text(str(n));sys.exit(0 if n>=3 else 1)"
    )
    step = TaskStep(name="flaky", adapter="generic", on_fail=FailPolicy.RETRY, retries=2,
                    params={"command": PY, "args": ["-c", code, str(counter)], "hide_window": True})
    rep = runner.run(TaskChain(name="t", steps=[step, py_step("next", "pass")]), ctx)
    assert rep.results[0].status == StepStatus.SUCCESS
    assert rep.results[0].attempts == 3
    assert rep.results[1].status == StepStatus.SUCCESS


def test_abort_stops_chain_but_restores_hooks(runner, ctx):
    chain = TaskChain(
        name="t",
        pre_hooks=[HookSpec(type="test_record", params={"tag": "a"}), HookSpec(type="test_record", params={"tag": "b"})],
        post_actions=[HookSpec(type="test_record", params={"tag": "post"})],
        steps=[py_step("bad", "raise SystemExit(1)", on_fail=FailPolicy.ABORT), py_step("never", "pass")],
    )
    rep = runner.run(chain, ctx)
    assert rep.aborted and len(rep.results) == 1
    assert RecordHook.events == [("apply", "a"), ("apply", "b"), ("restore", "b"), ("restore", "a"), ("apply", "post")]


def test_timeout_kills_process(runner, ctx):
    step = py_step("slow", "import time; time.sleep(60)", timeout=1.5)
    t0 = time.monotonic()
    rep = runner.run(TaskChain(name="t", steps=[step]), ctx)
    assert rep.results[0].status == StepStatus.TIMEOUT
    assert time.monotonic() - t0 < 15


def test_cancel_restores_and_skips_post_actions(runner, ctx):
    chain = TaskChain(
        name="t",
        pre_hooks=[HookSpec(type="test_record", params={"tag": "a"})],
        post_actions=[HookSpec(type="test_record", params={"tag": "post"})],
        steps=[py_step("slow", "import time; time.sleep(60)"), py_step("never", "pass")],
    )
    threading.Timer(1.0, ctx.cancel_event.set).start()
    rep = runner.run(chain, ctx)
    assert rep.cancelled
    assert rep.results[0].status == StepStatus.CANCELLED
    assert RecordHook.events == [("apply", "a"), ("restore", "a")]


def test_marker_file_completion(runner, ctx):
    code = "import sys,pathlib,time; time.sleep(0.5); pathlib.Path(sys.argv[1]).write_text('done', encoding='utf-8')"
    step = TaskStep(name="m", adapter="generic", timeout=20, params={
        "command": PY, "args": ["-c", code, "{work_dir}/m.flag"], "hide_window": True,
        "completion": "marker_file", "marker_file": "{work_dir}/m.flag",
    })
    rep = runner.run(TaskChain(name="t", steps=[step]), ctx)
    assert rep.results[0].status == StepStatus.SUCCESS


def test_log_keyword_completion(runner, ctx, tmp_path):
    log = tmp_path / "s.log"
    code = ("import sys,time;f=open(sys.argv[1],'a',encoding='utf-8');"
            "f.write('開始\\n');f.flush();time.sleep(0.5);f.write('任務全部完成\\n');f.flush();time.sleep(30)")
    step = TaskStep(name="l", adapter="generic", timeout=20, params={
        "command": PY, "args": ["-c", code, str(log)], "hide_window": True,
        "completion": "log_keyword", "log_file": str(log), "success_keywords": ["任務全部完成"],
    })
    t0 = time.monotonic()
    rep = runner.run(TaskChain(name="t", steps=[step]), ctx)
    assert rep.results[0].status == StepStatus.SUCCESS
    assert time.monotonic() - t0 < 15  # 成功後應主動結束仍在執行的程序


def test_unknown_adapter_and_disabled_step(runner, ctx):
    steps = [TaskStep(name="x", adapter="nope"), py_step("off", "pass", enabled=False)]
    rep = runner.run(TaskChain(name="t", steps=steps), ctx)
    assert rep.results[0].status == StepStatus.FAILED
    assert rep.results[1].status == StepStatus.SKIPPED


def test_runner_is_mutually_exclusive(runner, tmp_path):
    ctx1 = RunContext(work_dir=tmp_path / "a")
    chain = TaskChain(name="t", steps=[py_step("slow", "import time; time.sleep(60)")])
    th = threading.Thread(target=runner.run, args=(chain, ctx1))
    th.start()
    time.sleep(0.5)
    with pytest.raises(RuntimeError):
        runner.run(chain, RunContext(work_dir=tmp_path / "b"))
    ctx1.cancel_event.set()
    th.join(10)
    assert not th.is_alive()


def test_yaml_adapter_defaults(tmp_path):
    (tmp_path / "demo.yaml").write_text(
        f"id: demo\nname: 示範\nbase: generic\ndefaults:\n  command: '{PY}'\n  args: ['-c', 'pass']\n  hide_window: true\n",
        encoding="utf-8",
    )
    (tmp_path / "broken.yaml").write_text("id: [\n", encoding="utf-8")
    reg = AdapterRegistry()
    reg.load_dir(tmp_path)
    assert reg.infos["demo"].name == "示範"
    assert len(reg.errors) == 1
    rep = ChainRunner(reg).run(TaskChain(name="t", steps=[TaskStep(name="d", adapter="demo")]),
                               RunContext(work_dir=tmp_path / "w"))
    assert rep.ok


def test_adapter_captures_before_killing_process(runner, ctx, tmp_path, monkeypatch):
    # 失敗截圖必須在適配器關閉腳本程序「之前」拍，且同一次嘗試只拍一次
    import gachahub.core.runner as runner_mod
    from gachahub.core import process as proc_mod

    order = []
    monkeypatch.setattr(runner_mod, "capture_screen", lambda path: order.append("capture") or True)
    real_kill = proc_mod.kill_tree
    monkeypatch.setattr(proc_mod, "kill_tree", lambda pid, timeout=5.0: (order.append("kill"), real_kill(pid, timeout)))
    ctx.failure_dir = tmp_path / "failures"
    step = py_step("slow", "import time; time.sleep(60)", timeout=1.5)
    rep = runner.run(TaskChain(name="t", steps=[step]), ctx)
    assert rep.results[0].status == StepStatus.TIMEOUT
    assert order[0] == "capture" and order.count("capture") == 1 and "kill" in order
