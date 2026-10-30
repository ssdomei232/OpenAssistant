"""Push providers: DingTalk custom robot webhook (with signature) and null."""
from __future__ import annotations

import base64
import hashlib
import hmac
import time
import urllib.parse

import httpx


class DingTalkPush:
    """钉钉自定义机器人：Markdown 消息 + 加签 + 重试。"""

    name = "dingtalk"

    def __init__(self, webhook: str, secret: str = "", timeout_s: float = 10.0) -> None:
        self.webhook = webhook
        self.secret = secret
        self.timeout_s = timeout_s

    def is_configured(self) -> bool:
        return bool(self.webhook)

    def _signed_url(self) -> str:
        if not self.secret:
            return self.webhook
        timestamp = str(round(time.time() * 1000))
        string_to_sign = f"{timestamp}\n{self.secret}"
        digest = hmac.new(
            self.secret.encode("utf-8"), string_to_sign.encode("utf-8"), digestmod=hashlib.sha256
        ).digest()
        sign = urllib.parse.quote_plus(base64.b64encode(digest))
        joiner = "&" if "?" in self.webhook else "?"
        return f"{self.webhook}{joiner}timestamp={timestamp}&sign={sign}"

    def send(self, title: str, markdown: str) -> tuple[bool, str | None]:
        if not self.is_configured():
            return False, "not_configured"
        payload = {
            "msgtype": "markdown",
            "markdown": {"title": title, "text": markdown},
            "at": {"isAtAll": False},
        }
        try:
            with httpx.Client(timeout=self.timeout_s) as client:
                response = client.post(self._signed_url(), json=payload)
                response.raise_for_status()
                data = response.json()
        except Exception as exc:
            return False, f"{type(exc).__name__}: {exc}"
        if isinstance(data, dict) and data.get("errcode") not in (0, None):
            return False, f"dingtalk_error: {data.get('errcode')} {data.get('errmsg')}"
        return True, None


class NullPush:
    name = "null"

    def is_configured(self) -> bool:
        return False

    def send(self, title: str, markdown: str) -> tuple[bool, str | None]:
        return False, "not_configured"


class RecordingPush:
    """Test double that records outbound messages."""

    name = "recording"

    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def is_configured(self) -> bool:
        return True

    def send(self, title: str, markdown: str) -> tuple[bool, str | None]:
        self.messages.append((title, markdown))
        return True, None
