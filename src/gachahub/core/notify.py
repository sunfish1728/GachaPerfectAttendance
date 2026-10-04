"""通知設定與推播。僅使用標準庫連線，所有對外錯誤都不帶憑證。"""

from __future__ import annotations

import json
import logging
import math
import re
import smtplib
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from .models import ChainReport, StepStatus

log = logging.getLogger("gachahub")


class Channel(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    type: str
    name: str = ""
    enabled: bool = True
    params: dict[str, str] = Field(default_factory=dict)


class NotifyConfig(BaseModel):
    channels: list[Channel] = Field(default_factory=list)
    on_success: bool = True
    on_failure: bool = True
    on_cancel: bool = False
    on_schedule_skip: bool = True


class FieldSpec(BaseModel):
    key: str
    label: str
    secret: bool = False
    placeholder: str = ""
    required: bool = True
    help: str = ""
    default: str = ""


class ChannelSpec(BaseModel):
    type: str
    label: str
    fields: list[FieldSpec]


def _field(key, label, *, secret=False, default="", required=True, help="", placeholder=""):
    return FieldSpec(key=key, label=label, secret=secret, default=default,
                     required=required, help=help, placeholder=placeholder)


CHANNEL_TYPES: dict[str, ChannelSpec] = {
    spec.type: spec for spec in [
        ChannelSpec(type="telegram", label="Telegram", fields=[
            _field("bot_token", "機器人 Token", secret=True), _field("chat_id", "聊天 ID"),
            _field("api_base", "API 網址", default="https://api.telegram.org", required=False,
                   help="需要時可填自訂反向代理網址。"),
        ]),
        ChannelSpec(type="discord", label="Discord", fields=[
            _field("webhook_url", "Webhook 網址", secret=True),
        ]),
        ChannelSpec(type="serverchan", label="Server醬", fields=[
            _field("sendkey", "SendKey", secret=True, help="支援 Turbo SCT 與新版 sctp 密鑰。"),
        ]),
        ChannelSpec(type="bark", label="Bark", fields=[
            _field("server", "伺服器網址", default="https://api.day.app", required=False),
            _field("device_key", "裝置密鑰", secret=True),
        ]),
        ChannelSpec(type="email", label="電子郵件", fields=[
            _field("smtp_host", "SMTP 主機"), _field("smtp_port", "連接埠", default="465"),
            _field("security", "連線加密", default="ssl", help="ssl、starttls 或 none"),
            _field("username", "帳號", required=False),
            _field("password", "密碼", secret=True, required=False),
            _field("from_addr", "寄件地址", required=False, help="留空時使用帳號。"),
            _field("to_addr", "收件地址", help="多個地址以逗號分隔。"),
        ]),
        ChannelSpec(type="webhook", label="自訂 Webhook", fields=[
            _field("url", "網址"), _field("method", "方法", default="POST", help="POST 或 GET"),
            _field("body_template", "JSON 模板", default='{"title": "{title}", "body": "{body}"}',
                   help="在 JSON 字串中使用 {title}、{body}；GET 將物件轉成查詢參數。"),
        ]),
    ]
}


def load_config(path) -> NotifyConfig:
    path = Path(path)
    if not path.exists():
        return NotifyConfig()
    try:
        return NotifyConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError, yaml.YAMLError):
        # 不記錄解析例外；例外可能包含整段密鑰或 YAML 原文。
        log.warning("通知設定無法讀取，保留備份並使用預設：%s", path)
        backup = path.with_name(path.name + ".bak")
        if backup.exists():
            backup = path.with_name(path.name + f".{uuid.uuid4().hex}.bak")
        try:
            path.replace(backup)
        except OSError:
            log.warning("無法保留通知設定備份：%s", path)
        return NotifyConfig()


def save_config(path, config):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(yaml.safe_dump(config.model_dump(mode="json"), allow_unicode=True,
                                      sort_keys=False), encoding="utf-8")
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def _template(template: str, title: str, body: str) -> dict:
    # 先解析再代入字串，最後重新編碼；引號、換行與反斜線不會破壞 JSON。
    def replace(value):
        if isinstance(value, str):
            return re.sub(r"\{(title|body)\}", lambda m: title if m[1] == "title" else body, value)
        if isinstance(value, list):
            return [replace(v) for v in value]
        if isinstance(value, dict):
            return {replace(k): replace(v) for k, v in value.items()}
        return value
    value = json.loads(template)
    if not isinstance(value, dict):
        raise ValueError("模板必須是 JSON 物件")
    return replace(value)


def _http(url, payload, timeout, *, method="POST", form=False, channel_type=""):
    if urllib.parse.urlsplit(url).scheme not in ("http", "https"):
        return False, "網址必須以 http:// 或 https:// 開頭"
    headers = {"User-Agent": "gachahub"}
    data = None
    if method == "GET":
        parts = urllib.parse.urlsplit(url)
        query = urllib.parse.urlencode({k: v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
                                       for k, v in payload.items()})
        url = urllib.parse.urlunsplit(parts._replace(query=parts.query + ("&" if parts.query else "") + query))
    elif form:
        data = urllib.parse.urlencode(payload).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded; charset=utf-8"
    else:
        data = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if not 200 <= response.status < 300:
            return False, f"通知服務回傳 HTTP {response.status}"
        if channel_type in ("telegram", "serverchan", "bark"):
            result = json.loads(response.read(65537).decode("utf-8"))
            if not isinstance(result, dict):
                return False, "通知服務回應格式錯誤"
            success = (result.get("ok") is True if channel_type == "telegram" else
                       result.get("code") == (200 if channel_type == "bark" else 0))
            if not success:
                return False, "通知服務拒絕請求，請檢查憑證、收件者與額度"
    return True, "通知已送出"


def _email(params, title, body, timeout):
    security = params["security"].lower()
    if security not in ("ssl", "starttls", "none"):
        return False, "郵件加密方式必須是 ssl、starttls 或 none"
    recipients = [addr.strip() for addr in params["to_addr"].split(",") if addr.strip()]
    sender = params.get("from_addr") or params.get("username")
    if not recipients or not sender:
        return False, "請填寫寄件與收件地址"
    try:
        port = int(params["smtp_port"])
    except ValueError:
        return False, "SMTP 連接埠必須是整數"
    if not 1 <= port <= 65535:
        return False, "SMTP 連接埠必須介於 1 到 65535"
    message = EmailMessage()
    message["Subject"] = title
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    message.set_content(body, charset="utf-8")
    deadline = time.monotonic() + timeout
    client = None

    def remaining():
        value = deadline - time.monotonic()
        if value <= 0:
            raise TimeoutError
        if client is not None and getattr(client, "sock", None) is not None:
            client.sock.settimeout(value)
        return value

    try:
        factory = smtplib.SMTP_SSL if security == "ssl" else smtplib.SMTP
        kwargs = {"context": ssl.create_default_context()} if security == "ssl" else {}
        client = factory(params["smtp_host"], port, timeout=remaining(), **kwargs)
        if security == "starttls":
            remaining()
            client.starttls(context=ssl.create_default_context())
        if params.get("username"):
            remaining()
            client.login(params["username"], params.get("password", ""))
        remaining()
        refused = client.send_message(message, from_addr=sender, to_addrs=recipients)
        if refused:
            return False, "部分收件地址被郵件伺服器拒絕"
        return True, "通知郵件已送出"
    finally:
        # close 不等待 QUIT 的網路回應。
        if client is not None:
            client.close()


def send(channel: Channel, title: str, body: str, timeout: float = 10) -> tuple[bool, str]:
    try:
        if not math.isfinite(timeout) or timeout <= 0:
            return False, "通知等待逾時"
        spec = CHANNEL_TYPES.get(channel.type)
        if spec is None:
            return False, "不支援的通知管道"
        params = {field.key: channel.params.get(field.key) or field.default for field in spec.fields}
        missing = [f.label for f in spec.fields if f.required and not params[f.key].strip()]
        if missing:
            return False, "請填寫：" + "、".join(missing)
        text = title + "\n\n" + body
        if channel.type == "email":
            return _email(params, title, body, timeout)
        if channel.type == "telegram":
            url = params["api_base"].rstrip("/") + "/bot" + params["bot_token"] + "/sendMessage"
            return _http(url, {"chat_id": params["chat_id"], "text": text}, timeout, channel_type="telegram")
        if channel.type == "discord":
            return _http(params["webhook_url"], {"content": text[:2000]}, timeout)
        if channel.type == "serverchan":
            key = params["sendkey"]
            if key.startswith("sctp"):
                match = re.match(r"^sctp([0-9]+)t\S+$", key)
                if match is None:
                    return False, "Server醬新版 SendKey 格式錯誤"
                url = f"https://{match[1]}.push.ft07.com/send/{key}.send"
            else:
                url = f"https://sctapi.ftqq.com/{key}.send"
            return _http(url, {"title": title.replace("\n", " ").replace("\r", " ")[:32], "desp": body},
                         timeout, form=True, channel_type="serverchan")
        if channel.type == "bark":
            return _http(params["server"].rstrip("/") + "/push",
                         {"title": title, "body": body, "device_key": params["device_key"],
                          "group": "二遊全勤君"}, timeout, channel_type="bark")
        method = params["method"].upper()
        if method not in ("POST", "GET"):
            return False, "Webhook 方法必須是 POST 或 GET"
        try:
            payload = _template(params["body_template"], title, body)
        except (ValueError, TypeError, RecursionError):
            return False, "Webhook 模板必須是有效的 JSON 物件"
        return _http(params["url"], payload, timeout, method=method)
    except urllib.error.HTTPError as exc:
        return False, f"通知服務回傳 HTTP {exc.code}"
    except TimeoutError:
        return False, "通知等待逾時"
    except urllib.error.URLError as exc:
        return False, "通知等待逾時" if isinstance(exc.reason, TimeoutError) else "無法連線至通知服務"
    except smtplib.SMTPAuthenticationError:
        return False, "SMTP 登入失敗，請檢查帳號與密碼"
    except smtplib.SMTPException:
        return False, "郵件發送失敗，請檢查 SMTP 設定與收件地址"
    except Exception:
        # 不轉述例外或服務回傳內容，避免 URL、憑證及密碼外洩。
        return False, "通知發送失敗，請檢查設定與通知服務回應"


def broadcast(config, event: str, title: str, body: str,
              timeout: float = 10) -> list[tuple[str, bool, str]]:
    if event not in ("success", "failure", "cancel", "schedule_skip", "test"):
        return []
    if event != "test" and not getattr(config, "on_" + event):
        return []
    channels = [c for c in config.channels if c.enabled]
    if not channels:
        return []

    def name(channel):
        spec = CHANNEL_TYPES.get(channel.type)
        return channel.name or (spec.label if spec else channel.type)

    if not math.isfinite(timeout) or timeout <= 0:
        return [(name(c), False, "通知等待逾時") for c in channels]
    deadline = time.monotonic() + timeout
    executor = ThreadPoolExecutor(max_workers=min(32, len(channels)), thread_name_prefix="notify")

    def deliver(channel):
        remaining = deadline - time.monotonic()
        return send(channel, title, body, remaining) if remaining > 0 else (False, "通知等待逾時")

    futures = []
    try:
        futures = [executor.submit(deliver, c) for c in channels]
        done, _ = wait(futures, timeout=max(0, deadline - time.monotonic()))
        results = []
        for channel, future in zip(channels, futures):
            if future not in done:
                result = (False, "通知等待逾時")
            else:
                try:
                    result = future.result()
                except Exception:
                    result = (False, "通知發送失敗")
            results.append((name(channel), *result))
        return results
    finally:
        # 不使用 with：它會等待所有執行緒結束，破壞整批等待上限。
        executor.shutdown(wait=False, cancel_futures=True)


def format_report(report: ChainReport, trigger: str = "manual") -> tuple[str, str, str]:
    if report.cancelled:
        event, title = "cancel", f"⏹ {report.chain} 已停止"
    elif report.ok:
        event, title = "success", f"✅ {report.chain} 完成"
    else:
        event, title = "failure", f"❌ {report.chain} 有步驟失敗"
    labels = {StepStatus.SUCCESS: "成功", StepStatus.FAILED: "失敗", StepStatus.TIMEOUT: "逾時",
              StepStatus.CANCELLED: "已停止", StepStatus.SKIPPED: "已略過"}
    lines = []
    for result in report.results:
        seconds = max(0, (result.finished_at - result.started_at).total_seconds())
        lines.append(f"• {result.step}：{labels[result.status]}（{seconds:.1f} 秒）")
        if result.status in (StepStatus.FAILED, StepStatus.TIMEOUT) and result.message:
            message = result.message[:300] + ("…" if len(result.message) > 300 else "")
            lines.append("  " + message)
    end = report.finished_at or datetime.now(tz=report.started_at.tzinfo)
    seconds = max(0, (end - report.started_at).total_seconds())
    source = {"manual": "手動", "schedule": "排程", "tray": "系統匣"}.get(trigger, trigger)
    lines.extend([f"總耗時：{seconds:.1f} 秒", f"觸發來源：{source}"])
    return event, title, "\n".join(lines)
