from types import SimpleNamespace

import pytest

from gachahub.adapters.generic import GenericAdapter
from gachahub.core import process
from gachahub.core.adapter import AdapterError
from gachahub.core.context import RunContext, StepTimeout


@pytest.mark.parametrize("minutes", [0.001, 0, -1])
def test_stall_grace_cleanup_and_disabled(tmp_path, monkeypatch, minutes):
    clock = [0]
    monkeypatch.setattr("gachahub.adapters.generic.time.monotonic", lambda: clock[0])
    proc = SimpleNamespace(pid=123, poll=lambda: None)
    killed = []
    monkeypatch.setattr(process, "launch", lambda *args, **kwargs: proc)
    monkeypatch.setattr(process, "kill_tree", killed.append)
    ctx = RunContext(tmp_path)
    def sleep(seconds):
        clock[0] += seconds
        if clock[0] >= 5:
            raise StepTimeout()
    monkeypatch.setattr(ctx, "sleep", sleep)
    error = AdapterError if minutes > 0 else StepTimeout
    with pytest.raises(error):
        GenericAdapter().run(ctx, {"command": "fake.exe", "completion": "log_keyword",
            "log_file": str(tmp_path / "log.txt"), "success_keywords": ["done"],
            "startup_grace": 3, "stall_minutes": minutes})
    assert clock[0] >= 3
    assert killed == [123]
