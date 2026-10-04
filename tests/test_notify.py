from datetime import datetime, timedelta
import json
import smtplib
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import pytest

from gachahub.core import notify
from gachahub.core.models import ChainReport, StepResult, StepStatus
from gachahub.core.notify import Channel, NotifyConfig


@pytest.fixture(autouse=True)
def no_real_connections(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("測試不可連真實網路")
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    monkeypatch.setattr(smtplib, "SMTP", forbidden)
    monkeypatch.setattr(smtplib, "SMTP_SSL", forbidden)


class Response:
    status = 200

    def __init__(self, data=None):
        self.data = data or {}

    def read(self, size):
        return json.dumps(self.data).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


@pytest.fixture
def requests(monkeypatch):
    calls = []

    def open_request(request, timeout):
        assert timeout > 0
        calls.append((request, timeout))
        if "telegram" in request.full_url or "/sendMessage" in request.full_url:
            return Response({"ok": True})
        if request.full_url.endswith("/push"):
            return Response({"code": 200})
        return Response({"code": 0})

    monkeypatch.setattr(urllib.request, "urlopen", open_request)
    return calls


@pytest.mark.parametrize("kind,params,url,payload", [
    ("telegram", {"bot_token": "secret", "chat_id": "123"},
     "https://api.telegram.org/botsecret/sendMessage", {"chat_id": "123", "text": "標題\n\n內容"}),
    ("telegram", {"bot_token": "secret", "chat_id": "123", "api_base": "https://proxy.example/"},
     "https://proxy.example/botsecret/sendMessage", {"chat_id": "123", "text": "標題\n\n內容"}),
    ("discord", {"webhook_url": "https://discord.example/token"},
     "https://discord.example/token", {"content": "標題\n\n內容"}),
    ("serverchan", {"sendkey": "SCTsecret"}, "https://sctapi.ftqq.com/SCTsecret.send",
     {"title": ["標題"], "desp": ["內容"]}),
    ("serverchan", {"sendkey": "sctp123tsecret"}, "https://123.push.ft07.com/send/sctp123tsecret.send",
     {"title": ["標題"], "desp": ["內容"]}),
    ("bark", {"device_key": "secret"}, "https://api.day.app/push",
     {"title": "標題", "body": "內容", "device_key": "secret", "group": "二遊全勤君"}),
    ("bark", {"device_key": "secret", "server": "https://bark.example/"}, "https://bark.example/push",
     {"title": "標題", "body": "內容", "device_key": "secret", "group": "二遊全勤君"}),
    ("webhook", {"url": "https://example.com"}, "https://example.com", {"title": "標題", "body": "內容"}),
])
def test_http_requests(requests, kind, params, url, payload):
    assert notify.send(Channel(type=kind, params=params), "標題", "內容", timeout=2)[0]
    request, timeout = requests[0]
    assert request.full_url == url and request.get_method() == "POST" and timeout == 2
    if kind == "serverchan":
        assert "application/x-www-form-urlencoded" in request.get_header("Content-type")
        assert urllib.parse.parse_qs(request.data.decode()) == payload
    else:
        assert "application/json" in request.get_header("Content-type")
        assert json.loads(request.data) == payload


def test_discord_length(requests):
    notify.send(Channel(type="discord", params={"webhook_url": "https://discord.example"}), "t", "中" * 2500)
    assert len(json.loads(requests[0][0].data)["content"]) == 2000


def test_webhook_escaping_and_get(requests):
    title = '引號"\\\n{body}'
    body = '\n"}, "injected": true, "x": "\\'
    channel = Channel(type="webhook", params={"url": "https://example.com", "body_template":
                      '{"title":"前綴 {title}","nested":["{body}"], "bool":true}'})
    assert notify.send(channel, title, body)[0]
    assert json.loads(requests[-1][0].data) == {"title": "前綴 " + title, "nested": [body], "bool": True}
    channel.params = {"url": "https://example.com?original=1", "method": "GET"}
    assert notify.send(channel, title, body)[0]
    request = requests[-1][0]
    assert request.get_method() == "GET" and request.data is None
    assert urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query) == {
        "original": ["1"], "title": [title], "body": [body],
    }


@pytest.mark.parametrize("template", ["bad", "[]", '{"body": {body}}'])
def test_invalid_template(requests, template):
    result = notify.send(Channel(type="webhook", params={"url": "https://example.com",
                                                         "body_template": template}), "t", "b")
    assert not result[0] and "JSON" in result[1]
    assert not requests


@pytest.mark.parametrize("kind,data", [("telegram", {"ok": False, "description": "secret"}),
                                      ("serverchan", {"code": 1}), ("bark", {"code": 400})])
def test_api_failure(monkeypatch, kind, data):
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: Response(data))
    params = {"bot_token": "secret", "chat_id": "1", "sendkey": "secret", "device_key": "secret"}
    ok, message = notify.send(Channel(type=kind, params=params), "t", "b")
    assert not ok and "secret" not in message


@pytest.mark.parametrize("kind,params", [
    ("telegram", {"bot_token": "TOPSECRET", "chat_id": "1"}),
    ("discord", {"webhook_url": "https://example.com/TOPSECRET"}),
    ("serverchan", {"sendkey": "TOPSECRET"}),
    ("bark", {"device_key": "TOPSECRET"}),
])
def test_exception_secrets_hidden(monkeypatch, kind, params, caplog):
    def explode(request, timeout):
        raise RuntimeError(f"TOPSECRET {request.full_url} {request.data}")
    monkeypatch.setattr(urllib.request, "urlopen", explode)
    ok, message = notify.send(Channel(type=kind, params=params), "t", "b")
    assert not ok and "TOPSECRET" not in message + caplog.text
    for field in notify.CHANNEL_TYPES[kind].fields:
        if field.secret:
            assert params[field.key] not in message


@pytest.mark.parametrize("error,text", [
    (TimeoutError("TOPSECRET"), "逾時"),
    (urllib.error.URLError(TimeoutError("TOPSECRET")), "逾時"),
    (urllib.error.URLError("TOPSECRET"), "無法連線"),
    (urllib.error.HTTPError("https://example/TOPSECRET", 401, "TOPSECRET", {}, None), "401"),
])
def test_network_errors(monkeypatch, error, text):
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(urllib.request, "urlopen", fail)
    ok, message = notify.send(Channel(type="webhook", params={"url": "https://example"}), "t", "b")
    assert not ok and text in message and "TOPSECRET" not in message


@pytest.mark.parametrize("security,port", [("ssl", "465"), ("starttls", "587"), ("none", "25")])
def test_email(monkeypatch, security, port):
    events = []

    class SMTP:
        sock = None

        def __init__(self, host, port, timeout, **kwargs):
            events.append(("open", host, port, timeout, kwargs))

        def starttls(self, **kwargs):
            events.append(("tls", kwargs))

        def login(self, user, password):
            events.append(("login", user, password))

        def send_message(self, message, from_addr, to_addrs):
            events.append(("send", message, from_addr, to_addrs))
            return {}

        def close(self):
            events.append(("close",))

    def ssl_factory(*args, **kwargs):
        events.append(("ssl",))
        return SMTP(*args, **kwargs)

    monkeypatch.setattr(smtplib, "SMTP", SMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", ssl_factory)
    channel = Channel(type="email", params={"smtp_host": "smtp.example.com", "smtp_port": port,
                      "security": security, "username": "sender@example.com", "password": "TOPSECRET",
                      "to_addr": "a@example.com, b@example.com"})
    assert notify.send(channel, "繁中主旨", "繁中內文")[0]
    assert any(e[0] == "ssl" for e in events) == (security == "ssl")
    assert any(e[0] == "tls" for e in events) == (security == "starttls")
    opening = next(e for e in events if e[0] == "open")
    assert opening[1:3] == ("smtp.example.com", int(port)) and 0 < opening[3] <= 10
    sending = next(e for e in events if e[0] == "send")
    assert str(sending[1]["Subject"]) == "繁中主旨"
    assert sending[1].get_content().strip() == "繁中內文"
    assert sending[1].get_content_charset() == "utf-8"
    assert sending[2:] == ("sender@example.com", ["a@example.com", "b@example.com"])
    assert events[-1] == ("close",)


def test_email_auth_failure(monkeypatch):
    closed = []

    class SMTP:
        sock = None

        def __init__(self, *args, **kwargs):
            pass

        def login(self, *args):
            raise smtplib.SMTPAuthenticationError(535, b"TOPSECRET")

        def close(self):
            closed.append(True)

    monkeypatch.setattr(smtplib, "SMTP_SSL", SMTP)
    ok, message = notify.send(Channel(type="email", params={"smtp_host": "smtp.example", "username": "user",
                                "password": "TOPSECRET", "to_addr": "a@example.com"}), "t", "b")
    assert not ok and "登入失敗" in message and "TOPSECRET" not in message
    assert closed


@pytest.mark.parametrize("event,flag", [("success", "on_success"), ("failure", "on_failure"),
                                       ("cancel", "on_cancel"), ("schedule_skip", "on_schedule_skip")])
def test_broadcast_filters(monkeypatch, event, flag):
    calls = []
    monkeypatch.setattr(notify, "send", lambda c, *a: (calls.append(c.id) is None, "已送出"))
    config = NotifyConfig(channels=[Channel(type="telegram", name="我的通知"),
                                    Channel(type="discord", enabled=False)])
    setattr(config, flag, False)
    assert notify.broadcast(config, event, "t", "b") == []
    setattr(config, flag, True)
    assert notify.broadcast(config, event, "t", "b") == [("我的通知", True, "已送出")]
    setattr(config, flag, False)
    assert notify.broadcast(config, "test", "t", "b") == [("我的通知", True, "已送出")]
    assert len(calls) == 2
    assert notify.broadcast(config, "unknown", "t", "b") == []


def test_broadcast_parallel(monkeypatch):
    barrier = threading.Barrier(3)

    def send(channel, title, body, timeout):
        barrier.wait(timeout=1)
        return True, channel.id

    monkeypatch.setattr(notify, "send", send)
    channels = [Channel(type="bark", name=str(i)) for i in range(3)]
    result = notify.broadcast(NotifyConfig(channels=channels), "test", "t", "b", timeout=2)
    assert result == [(str(i), True, channel.id) for i, channel in enumerate(channels)]


def test_broadcast_timeout_does_not_wait_for_worker(monkeypatch):
    release = threading.Event()
    finished = threading.Event()

    def send(channel, *args):
        try:
            if channel.name == "慢":
                release.wait(timeout=2)
            return True, "已送出"
        finally:
            if channel.name == "慢":
                finished.set()

    monkeypatch.setattr(notify, "send", send)
    config = NotifyConfig(channels=[Channel(type="bark", name="快"), Channel(type="bark", name="慢")])
    try:
        start = time.monotonic()
        result = notify.broadcast(config, "test", "t", "b", timeout=0.1)
        assert time.monotonic() - start < 0.6
        assert result == [("快", True, "已送出"), ("慢", False, "通知等待逾時")]
    finally:
        release.set()
        assert finished.wait(timeout=2)


def test_broadcast_worker_error(monkeypatch):
    def fail(*args):
        raise RuntimeError("SECRET")
    monkeypatch.setattr(notify, "send", fail)
    assert notify.broadcast(NotifyConfig(channels=[Channel(type="bark")]), "test", "t", "b") == [
        ("Bark", False, "通知發送失敗")]


@pytest.mark.parametrize("status,cancelled,aborted,event,word", [
    (StepStatus.SUCCESS, False, False, "success", "完成"),
    (StepStatus.SKIPPED, False, False, "success", "完成"),
    (StepStatus.FAILED, False, False, "failure", "有步驟失敗"),
    (StepStatus.TIMEOUT, False, True, "failure", "有步驟失敗"),
    (StepStatus.CANCELLED, True, True, "cancel", "已停止"),
])
def test_format_report(status, cancelled, aborted, event, word):
    start = datetime(2026, 10, 1)
    report = ChainReport(chain="每日清體力", started_at=start, finished_at=start + timedelta(seconds=10),
                         cancelled=cancelled, aborted=aborted, results=[StepResult(
                             step="絕區零一條龍", status=status, started_at=start,
                             finished_at=start + timedelta(seconds=5), message="錯" * 400)])
    actual, title, body = notify.format_report(report, "schedule")
    assert actual == event and word in title and "每日清體力" in title
    assert "絕區零一條龍" in body and "5.0 秒" in body and "總耗時：10.0 秒" in body
    assert "觸發來源：排程" in body
    if status in (StepStatus.FAILED, StepStatus.TIMEOUT):
        assert "錯" * 300 + "…" in body and "錯" * 301 not in body
    else:
        assert "錯" not in body


def test_format_unfinished():
    report = ChainReport(chain="test", started_at=datetime.now() - timedelta(seconds=1))
    assert "觸發來源：手動" in notify.format_report(report)[2]
    assert "觸發來源：系統匣" in notify.format_report(report, "tray")[2]


def test_config_roundtrip(tmp_path):
    path = tmp_path / "data" / "notify.yaml"
    assert notify.load_config(path) == NotifyConfig()
    config = NotifyConfig(channels=[Channel(type="bark", name="手機", params={"device_key": "TOPSECRET"})],
                          on_cancel=True)
    notify.save_config(path, config)
    assert notify.load_config(path) == config
    assert "手機" in path.read_text(encoding="utf-8")
    assert not list(path.parent.glob("*.tmp"))
    assert Channel(type="bark").id != Channel(type="bark").id
    other = NotifyConfig()
    other.channels.append(Channel(type="bark"))
    assert NotifyConfig().channels == []


@pytest.mark.parametrize("text", ["[broken", "channels: 123", "channels:\n  - type: bark\n    params: []", "null"])
def test_corrupt_config_backup(tmp_path, text, caplog):
    path = tmp_path / "notify.yaml"
    path.write_text(text, encoding="utf-8")
    assert notify.load_config(path) == NotifyConfig()
    assert path.with_name("notify.yaml.bak").read_text(encoding="utf-8") == text
    assert not path.exists()
    assert "預設" in caplog.text


def test_save_failure_keeps_previous_file(tmp_path, monkeypatch):
    path = tmp_path / "notify.yaml"
    notify.save_config(path, NotifyConfig(on_success=False))
    from pathlib import Path

    def fail(*args):
        raise OSError("cannot replace")
    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(OSError):
        notify.save_config(path, NotifyConfig())
    assert not notify.load_config(path).on_success
    assert not list(tmp_path.glob("*.tmp"))


def test_validation_and_schema(requests):
    assert set(notify.CHANNEL_TYPES) == {"telegram", "discord", "serverchan", "bark", "email", "webhook"}
    assert not notify.send(Channel(type="unknown"), "t", "b")[0]
    assert "請填寫" in notify.send(Channel(type="telegram"), "t", "b")[1]
    assert "格式錯誤" in notify.send(Channel(type="serverchan", params={"sendkey": "sctpbad"}), "t", "b")[1]
    assert not notify.send(Channel(type="webhook", params={"url": "file:///secret"}), "t", "b")[0]
    assert not notify.send(Channel(type="webhook", params={"url": "https://example", "method": "PUT"}), "t", "b")[0]
    assert not notify.send(Channel(type="webhook", params={"url": "https://example"}), "t", "b", timeout=0)[0]
    assert not requests


def test_config_invalid_utf8_and_repeated_backup(tmp_path, caplog):
    path = tmp_path / "notify.yaml"
    path.write_bytes(b"\xffTOPSECRET")
    assert notify.load_config(path) == NotifyConfig()
    assert "TOPSECRET" not in caplog.text
    path.write_text("[broken", encoding="utf-8")
    assert notify.load_config(path) == NotifyConfig()
    assert len(list(tmp_path.glob("*.bak"))) == 2


def test_email_sender_override_and_refused_recipient(monkeypatch):
    calls = []

    class SMTP:
        sock = None

        def __init__(self, *args, **kwargs):
            pass

        def send_message(self, message, from_addr, to_addrs):
            calls.append((from_addr, to_addrs))
            return {"b@example.com": (550, b"TOPSECRET")}

        def close(self):
            pass

    monkeypatch.setattr(smtplib, "SMTP", SMTP)
    params = {"smtp_host": "smtp.example", "security": "none", "from_addr": "custom@example.com",
              "to_addr": "a@example.com,b@example.com"}
    ok, message = notify.send(Channel(type="email", params=params), "t", "b")
    assert not ok and "部分收件" in message and "TOPSECRET" not in message
    assert calls == [("custom@example.com", ["a@example.com", "b@example.com"])]


@pytest.mark.parametrize("params,message", [
    ({"smtp_port": "invalid"}, "必須是整數"),
    ({"smtp_port": "65536"}, "65535"),
    ({"security": "unknown"}, "加密方式"),
    ({"from_addr": ""}, "寄件與收件"),
])
def test_email_invalid_settings(params, message):
    values = {"smtp_host": "smtp.example", "from_addr": "a@example.com", "to_addr": "b@example.com"}
    values.update(params)
    ok, text = notify.send(Channel(type="email", params=values), "t", "b")
    assert not ok and message in text


def test_zero_timeout_broadcast(requests):
    config = NotifyConfig(channels=[Channel(type="bark"), Channel(type="unknown", name="自訂")])
    assert notify.broadcast(config, "test", "t", "b", timeout=0) == [
        ("Bark", False, "通知等待逾時"), ("自訂", False, "通知等待逾時")]
    assert not requests
