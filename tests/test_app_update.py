import io
import json
import urllib.error
import urllib.request

import pytest

from gachahub.core.app_update import check_github


@pytest.mark.parametrize("tag,current,available", [
    ("v0.2.0", "0.1.0", True), ("v0.1.0", "0.1", False),
    ("v0.1.0", "0.2.0", False), ("v2.1-beta", "2.0.9", True),
])
def test_release(monkeypatch, tag, current, available):
    def fake(request, timeout):
        assert request.full_url == "https://api.github.com/repos/owner/name/releases/latest"
        assert request.get_header("User-agent") and timeout == 7
        return io.BytesIO(json.dumps({"tag_name": tag, "html_url": "https://example.test/release",
                                     "body": "更新說明"}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    result = check_github("owner/name", current, timeout=7)
    assert result.available == available and result.error is None
    assert result.latest == tag and result.notes == "更新說明"


@pytest.mark.parametrize("repo", ["", "owner", "owner/name/extra", "https://github.com/a/b", "../name", "a/b?x"])
def test_invalid_repo(monkeypatch, repo):
    def unexpected(*args, **kwargs):
        pytest.fail("不應發出請求")

    monkeypatch.setattr(urllib.request, "urlopen", unexpected)
    result = check_github(repo, "0.1.0")
    assert not result.available and result.error


@pytest.mark.parametrize("error, message", [
    (urllib.error.HTTPError("https://example.test", 404, "missing", {}, None), "此倉庫尚無發布版本"),
    (urllib.error.HTTPError("https://example.test", 403, "forbidden", {}, None), "HTTP 403"),
    (TimeoutError("逾時"), "逾時"), (urllib.error.URLError("離線"), "離線"),
])
def test_request_failure(monkeypatch, error, message):
    def fake(*args, **kwargs):
        raise error

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    result = check_github("owner/name", "0.1.0")
    assert not result.available and message in result.error


@pytest.mark.parametrize("data", [b"bad json", b"[]", b'{}', b'{"tag_name": "latest"}',
                                   b'{"tag_name": 123}', b'{"tag_name": "2.0", "body": []}'])
def test_invalid_response(monkeypatch, data):
    monkeypatch.setattr(urllib.request, "urlopen", lambda *args, **kwargs: io.BytesIO(data))
    result = check_github("owner/name", "0.1")
    assert not result.available and "檢查更新失敗" in result.error
