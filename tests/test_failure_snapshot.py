import os

import pytest

from gachahub.core import runner as module
from gachahub.core.adapter import AdapterError
from gachahub.core.context import Cancelled, RunContext, StepTimeout
from gachahub.core.models import FailPolicy, StepStatus, TaskChain, TaskStep


@pytest.mark.parametrize("error,status,take", [
    (AdapterError("失敗"), StepStatus.FAILED, True),
    (StepTimeout(), StepStatus.TIMEOUT, True),
    (Cancelled(), StepStatus.CANCELLED, False),
    (None, StepStatus.SUCCESS, False),
])
@pytest.mark.parametrize("enabled", [True, False])
def test_capture_before_cleanup(tmp_path, monkeypatch, error, status, take, enabled):
    events, logs = [], []
    class Adapter:
        def validate(self, params):
            pass
        def run(self, ctx, params):
            if error:
                raise error
            return "完成"
        def cleanup(self, ctx, params):
            events.append("cleanup")
    class Registry:
        def create(self, name):
            return Adapter()
    def capture(path):
        events.append("capture")
        assert path.parent == tmp_path / "failures"
        assert "第1次.png" in path.name
        assert not any(c in path.name for c in '/\\:*?"<>|')
        path.write_bytes(b"png")
        return True
    monkeypatch.setattr(module, "capture_screen", capture)
    ctx = RunContext(tmp_path / "work", on_log=logs.append,
                     failure_dir=tmp_path / "failures" if enabled else None)
    report = module.ChainRunner(Registry()).run(TaskChain(name="t", steps=[
        TaskStep(name='a/b:c*?"<>|', adapter="fake")]), ctx)
    assert report.results[0].status == status
    assert events == (["capture", "cleanup"] if take and enabled else ["cleanup"])
    if take and enabled:
        assert any("失敗截圖：" in line for line in logs)


def test_retention_and_exceptions(tmp_path, monkeypatch):
    ctx = RunContext(tmp_path, failure_dir=tmp_path / "failures")
    ctx.failure_dir.mkdir()
    for i in range(55):
        path = ctx.failure_dir / f"old{i:02}.png"
        path.touch()
        os.utime(path, (i + 1, i + 1))
    other = ctx.failure_dir / "keep.txt"
    other.touch()
    monkeypatch.setattr(module, "capture_screen", lambda path: path.write_bytes(b"png") or True)
    module.ChainRunner._capture_failure(ctx, "bad", 2)
    images = list(ctx.failure_dir.glob("*.png"))
    assert len(images) == 50 and other.exists()
    assert not (ctx.failure_dir / "old05.png").exists()
    assert (ctx.failure_dir / "old06.png").exists()
    def broken(path):
        raise RuntimeError("截圖失敗")
    monkeypatch.setattr(module, "capture_screen", broken)
    module.ChainRunner._capture_failure(ctx, "bad", 1)  # 例外不向外傳


def test_each_retry_captured(tmp_path, monkeypatch):
    paths = []
    class Adapter:
        def validate(self, params): pass
        def run(self, ctx, params): raise AdapterError("失敗")
        def cleanup(self, ctx, params): pass
    class Registry:
        def create(self, name): return Adapter()
    monkeypatch.setattr(module, "capture_screen", lambda path: paths.append(path) or False)
    runner = module.ChainRunner(Registry())
    runner.retry_delay = 0
    runner.run(TaskChain(name="t", steps=[TaskStep(name="s", adapter="fake",
        on_fail=FailPolicy.RETRY, retries=2)]), RunContext(tmp_path, failure_dir=tmp_path / "failures"))
    assert len(paths) == 3
    assert [f"第{i}次.png" in path.name for i, path in enumerate(paths, 1)] == [True] * 3


def test_capture_exception_does_not_skip_cleanup(tmp_path, monkeypatch):
    cleaned = []
    class Adapter:
        def validate(self, params): pass
        def run(self, ctx, params): raise AdapterError("原本的失敗")
        def cleanup(self, ctx, params): cleaned.append(True)
    class Registry:
        def create(self, name): return Adapter()
    def broken(path):
        raise OSError("無法截圖")
    monkeypatch.setattr(module, "capture_screen", broken)
    result = module.ChainRunner(Registry()).run(TaskChain(name="t", steps=[
        TaskStep(name="bad", adapter="fake")]), RunContext(tmp_path, failure_dir=tmp_path / "failures"))
    assert cleaned == [True]
    assert result.results[0].message == "原本的失敗"
