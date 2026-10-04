import json

import pytest

from gachahub.adapters.ok_script import OkScriptAdapter
from gachahub.adapters.onedragon import OneDragonAdapter
from gachahub.core import compat
from gachahub.core.adapter import Adapter, AdapterError
from gachahub.core.context import RunContext
from gachahub.core.models import FailPolicy, StepStatus, TaskChain, TaskStep
from gachahub.core.registry import AdapterInfo, AdapterRegistry
from gachahub.core.runner import ChainRunner


class FakeAdapter(Adapter):
    id = "fake"

    def script_version(self, params):
        return params.get("version")

    def run(self, ctx, params):
        return "完成"


@pytest.mark.parametrize("text, expected", [
    ("v1.4.6", (1, 4, 6)), ("1.4.6-beta", (1, 4, 6)),
    ("2.0.0", (2, 0, 0)), ("v2.1", (2, 1)), (" v2.1 ", (2, 1)),
    ("", None), ("latest", None), ("1..4", None), ("1.4oops", None), (None, None),
])
def test_parse(text, expected):
    assert compat.parse_version(text) == expected


@pytest.mark.parametrize("version, ok", [
    ("v1.3.99", False), ("v1.4", True), ("v1.4.0", True),
    ("v1.4.999", True), ("v1.5.0", False), ("v2.0.1", False),
])
def test_range(version, ok):
    info = AdapterInfo("demo", "示範", "generic", compat={
        "min": "v1.4.0", "max": "v1.4", "on_mismatch": "block", "note": "已驗證"})
    result = compat.check(info, FakeAdapter(), {"version": version})
    assert result.ok == ok and result.block == (not ok)
    if not ok:
        assert "超出已驗證範圍" in result.message and "已驗證" in result.message


def test_optional_unreadable_and_warn():
    info = AdapterInfo("demo", "示範", "generic")
    assert compat.check(info, FakeAdapter(), {}).ok
    info.compat = {"max": "1.4.6", "on_mismatch": "warn"}
    for version in (None, "未知"):
        result = compat.check(info, FakeAdapter(), {"version": version})
        assert result.ok and "無法讀取版本" in result.message
    assert compat.check(info, FakeAdapter(), {"version": "1.4.6.8"}).ok
    result = compat.check(info, FakeAdapter(), {"version": "1.4.7"})
    assert not result.ok and not result.block


def test_adapter_versions(tmp_path):
    app = tmp_path / "data/apps/ok-nte"
    app.mkdir(parents=True)
    (tmp_path / "ok-nte.exe").touch()
    (app / "app.json").write_text(json.dumps({"current_version": "v1.4.6"}), encoding="utf-8")
    (tmp_path / "data/apps/other").mkdir()
    ok = OkScriptAdapter(defaults={"exe": "ok-nte.exe"})
    assert ok.script_version({"install_dir": str(tmp_path)}) == "v1.4.6"
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "2.0.0"', encoding="utf-8")
    dragon = OneDragonAdapter(defaults={"install_dir": str(tmp_path)})
    assert dragon.script_version({}) == "2.0.0"
    assert ok.script_version({}) is None
    assert dragon.script_version({"install_dir": None}) is None


def test_registry_metadata(tmp_path):
    (tmp_path / "old.yaml").write_text("id: old\nbase: generic\n", encoding="utf-8")
    (tmp_path / "new.yaml").write_text('id: new\nbase: generic\nversion: 1\ncompat:\n  max: "2.0"\n', encoding="utf-8")
    registry = AdapterRegistry()
    registry.load_dir(tmp_path)
    assert registry.infos["old"].version == 0 and registry.infos["old"].compat == {}
    assert registry.infos["new"].version == 1
    assert registry.infos["new"].compat["max"] == "2.0"


@pytest.mark.parametrize("mode, expected, attempts", [
    ("warn", StepStatus.SUCCESS, 1), ("block", StepStatus.FAILED, 0),
])
def test_runner_integration(tmp_path, monkeypatch, mode, expected, attempts):
    registry = AdapterRegistry()
    registry.infos["fake"] = AdapterInfo("fake", "示範", "generic", compat={"max": "1.4", "on_mismatch": mode})
    monkeypatch.setattr(registry, "create", lambda _: FakeAdapter())
    events, logs = [], []
    ctx = RunContext(work_dir=tmp_path, on_log=logs.append,
                     on_event=lambda kind, data: events.append((kind, data)))
    report = ChainRunner(registry).run(TaskChain(name="t", steps=[TaskStep(
        name="測試", adapter="fake", params={"version": "2.0"}, on_fail=FailPolicy.RETRY, retries=2)]), ctx)
    assert report.results[0].status == expected and report.results[0].attempts == attempts
    warnings = [data for kind, data in events if kind == "compat_warning"]
    assert len(warnings) == (1 if mode == "warn" else 0)
    if mode == "warn":
        assert warnings[0]["step"] == "測試" and any("警告" in log for log in logs)


def test_runner_check_exception_and_validate_order(tmp_path, monkeypatch):
    registry = AdapterRegistry()
    monkeypatch.setattr(registry, "create", lambda _: FakeAdapter())
    calls = []

    def broken(*args):
        calls.append("check")
        raise RuntimeError("壞掉")

    monkeypatch.setattr(compat, "check", broken)
    runner = ChainRunner(registry)
    step = TaskStep(name="t", adapter="generic")
    assert runner._run_step(step, RunContext(work_dir=tmp_path)).status == StepStatus.SUCCESS
    assert calls == ["check"]

    def invalid(self, params):
        raise AdapterError("參數錯誤")

    monkeypatch.setattr(FakeAdapter, "validate", invalid)
    assert runner._run_step(step, RunContext(work_dir=tmp_path)).attempts == 0
    assert calls == ["check"]
