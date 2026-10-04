import hashlib
import io
import json
import urllib.request

import pytest

from gachahub.core.adapter_repo import (
    MAX_DOWNLOAD, RemoteAdapter, RepoError, RepoIndex, apply, build_index, fetch_index, plan,
)
from gachahub.core.registry import AdapterInfo, AdapterRegistry


def repository(tmp_path, content=b"id: demo\nname: test\nbase: generic\nversion: 2\n"):
    remote = tmp_path / "remote"
    remote.mkdir()
    (remote / "demo.yaml").write_bytes(content)
    entry = RemoteAdapter(id="demo", name="示範", version=2, file="demo.yaml",
                          sha256=hashlib.sha256(content).hexdigest())
    index = RepoIndex(updated="2026-10-04", adapters=[entry])
    path = remote / "index.json"
    path.write_text(index.model_dump_json(), encoding="utf-8")
    return path.as_uri(), index


def test_file_roundtrip_and_backup(tmp_path):
    url, _ = repository(tmp_path)
    index = fetch_index(url)
    local = tmp_path / "local"
    local.mkdir()
    original = b"id: demo\nbase: generic\nversion: 1\n"
    (local / "old-name.yaml").write_bytes(original)
    registry = AdapterRegistry()
    registry.load_dir(local)
    items = plan(index, registry.infos, "0.1.0")
    assert items[0].action == "update" and items[0].local_version == 1
    assert "更新完成" in apply(url, index, items, local)[0]
    assert (local / "old-name.yaml").read_bytes() != original
    assert not (local / "demo.yaml").exists()
    backups = list((local / ".backup").glob("*/old-name.yaml"))
    assert len(backups) == 1 and backups[0].read_bytes() == original
    registry.load_dir(local)
    assert registry.infos["demo"].version == 2
    assert plan(index, registry.infos, "0.1.0")[0].action == "same"


@pytest.mark.parametrize("filename", ["../evil.yaml", "..\\evil.yaml", "/evil.yaml", "C:evil.yaml",
                                           "x.py", "%2e%2e.yaml", "CON.yaml", "x.yaml#fragment"])
def test_path_traversal(tmp_path, filename):
    url, index = repository(tmp_path)
    index.adapters[0].file = filename
    results = apply(url, index, plan(index, {}, "0.1.0"), tmp_path / "local")
    assert "略過" in results[0] and "檔名" in results[0]
    assert not (tmp_path / "local").exists()


@pytest.mark.parametrize("content, message", [
    (b"id: other\nbase: generic\nversion: 2\n", "id"),
    (b"id: demo\nbase: missing\nversion: 2\n", "base"),
    (b"base: generic\n", "id"), (b"id: demo\nbase: generic\nversion: 1\n", "version"),
    (b"!!python/object:os.system {}", "略過"), (b"id: [\n", "略過"),
])
def test_invalid_yaml(tmp_path, content, message):
    url, index = repository(tmp_path, content)
    assert message in apply(url, index, plan(index, {}, "0.1"), tmp_path / "local")[0]
    assert not (tmp_path / "local/demo.yaml").exists()


def test_hash_failure_preserves_old_and_continues(tmp_path):
    url, index = repository(tmp_path)
    index.adapters[0].sha256 = "0" * 64
    data = b"id: second\nbase: generic\nversion: 1\n"
    (tmp_path / "remote/second.yaml").write_bytes(data)
    index.adapters.append(RemoteAdapter(id="second", name="第二", version=1,
                                       file="second.yaml", sha256=hashlib.sha256(data).hexdigest()))
    local = tmp_path / "local"
    local.mkdir()
    original = b"id: demo\nbase: generic\nversion: 1\n"
    (local / "demo.yaml").write_bytes(original)
    results = apply(url, index, plan(index, {}, "0.1"), local)
    assert "SHA256" in results[0] and "更新完成" in results[1]
    assert (local / "demo.yaml").read_bytes() == original
    assert not (local / ".backup").exists()


def test_plan_minimum_and_no_downgrade(tmp_path):
    _, index = repository(tmp_path)
    local = {"demo": AdapterInfo("demo", "示範", "generic", version=3)}
    assert plan(index, {}, "0.1")[0].action == "new"
    assert plan(index, local, "0.1")[0].action == "same"
    index.adapters[0].min_app = "v2.0.0"
    for current in ("1.99", "未知"):
        assert plan(index, {}, current)[0].action == "skip_app_too_old"
    assert plan(index, {}, "2.0")[0].action == "new"
    index.adapters[0].min_app = "錯誤"
    assert plan(index, {}, "3.0")[0].action == "skip_app_too_old"


def test_build_index(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    content = 'id: demo\nname: 示範\nbase: generic\nversion: 1\ncompat:\n  note: 已驗證\n'
    (source / "demo.yaml").write_text(content, encoding="utf-8")
    (source / "ignore.py").write_text("raise RuntimeError()", encoding="utf-8")
    output = build_index(source, tmp_path / "repo")
    index = fetch_index(output.as_uri())
    assert len(index.adapters) == 1 and index.adapters[0].note == "已驗證"
    assert (output.parent / "demo.yaml").read_bytes() == (source / "demo.yaml").read_bytes()
    assert not (output.parent / "ignore.py").exists()


def test_fetch_errors_and_size(tmp_path, monkeypatch):
    path = tmp_path / "index.json"
    for data in ("bad json", '{"schema": 2}', '{"schema": true}', '{}'):
        path.write_text(data, encoding="utf-8")
        with pytest.raises(RepoError):
            fetch_index(path.as_uri())
    with pytest.raises(RepoError):
        fetch_index("http://invalid/index.json")
    calls = []

    def large(request, timeout):
        calls.append(timeout)
        return io.BytesIO(b"x" * (MAX_DOWNLOAD + 1))

    monkeypatch.setattr(urllib.request, "urlopen", large)
    with pytest.raises(RepoError, match="1 MB"):
        fetch_index("https://example.test/index.json", timeout=4)
    assert calls == [4]


def test_same_filename_other_id_preserved(tmp_path):
    url, index = repository(tmp_path)
    local = tmp_path / "local"
    local.mkdir()
    content = "id: other\nbase: generic\n"
    (local / "demo.yaml").write_text(content, encoding="utf-8")
    assert "其他適配器" in apply(url, index, plan(index, {}, "0.1"), local)[0]
    assert (local / "demo.yaml").read_text(encoding="utf-8") == content


def test_atomic_replace_failure_preserves_old(tmp_path, monkeypatch):
    import gachahub.core.adapter_repo as module

    url, index = repository(tmp_path)
    local = tmp_path / "local"
    local.mkdir()
    original = b"id: demo\nbase: generic\nversion: 1\n"
    (local / "demo.yaml").write_bytes(original)

    def fail(*args):
        raise OSError("無法寫入")

    monkeypatch.setattr(module.os, "replace", fail)
    assert "略過" in apply(url, index, plan(index, {}, "0.1"), local)[0]
    assert (local / "demo.yaml").read_bytes() == original
    assert len(list((local / ".backup").glob("*/demo.yaml"))) == 1
    assert not list(local.glob(".adapter-*"))


def test_skip_app_and_missing_download(tmp_path):
    url, index = repository(tmp_path)
    index.adapters[0].min_app = "9.0"
    assert apply(url, index, plan(index, {}, "0.1"), tmp_path / "local") == []
    index.adapters[0].min_app = ""
    (tmp_path / "remote/demo.yaml").unlink()
    assert "下載倉庫內容失敗" in apply(url, index, plan(index, {}, "0.1"), tmp_path / "local")[0]


def test_duplicate_index_and_network_error(tmp_path, monkeypatch):
    url, index = repository(tmp_path)
    index.adapters.append(index.adapters[0])
    (tmp_path / "remote/index.json").write_text(index.model_dump_json(), encoding="utf-8")
    with pytest.raises(RepoError, match="重複"):
        fetch_index(url)

    def timeout(*args, **kwargs):
        raise TimeoutError("逾時")

    monkeypatch.setattr(urllib.request, "urlopen", timeout)
    with pytest.raises(RepoError, match="下載倉庫內容失敗.*逾時"):
        fetch_index("https://example.test/index.json")
