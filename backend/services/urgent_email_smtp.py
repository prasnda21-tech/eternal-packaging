"""Gmail SMTP sender for Urgent PO emails.

Uses stdlib smtplib only — no new dependency. Disabled until env secrets are set.
Never log credentials or raw passwords.
"""
from __future__ import annotations

import logging
import os
import re
import smtplib
import ssl
from dataclasses import dataclass
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional, Protocol

logger = logging.getLogger("urgent_email_smtp")

_SECRET_RE = re.compile(
    r"(password|passwd|pwd|secret|app[_-]?password|authorization)\s*[:=]\s*\S+",
    re.IGNORECASE,
)


def sanitize_smtp_error(exc: BaseException | str) -> str:
    text = str(exc or "")
    text = _SECRET_RE.sub(r"\1=[REDACTED]", text)
    # Strip accidental env dumps
    for key in (
        "URGENT_EMAIL_SMTP_PASSWORD",
        "SMTP_PASSWORD",
        "GMAIL_APP_PASSWORD",
    ):
        val = os.environ.get(key) or ""
        if val and val in text:
            text = text.replace(val, "[REDACTED]")
    return text[:400]


@dataclass(frozen=True)
class SmtpConfig:
    enabled: bool
    host: str
    port: int
    username: str
    password: str
    from_name: str
    from_addr: str
    use_ssl: bool
    app_base_url: str


def load_smtp_config() -> SmtpConfig:
    host = (os.environ.get("URGENT_EMAIL_SMTP_HOST") or "smtp.gmail.com").strip()
    port = int(os.environ.get("URGENT_EMAIL_SMTP_PORT") or "465")
    username = (os.environ.get("URGENT_EMAIL_SMTP_USER") or "eternalsabloncup@gmail.com").strip()
    password = (os.environ.get("URGENT_EMAIL_SMTP_PASSWORD") or "").strip()
    from_name = (
        os.environ.get("URGENT_EMAIL_FROM_NAME") or "Eternal Packaging — Notification"
    ).strip()
    from_addr = (os.environ.get("URGENT_EMAIL_FROM_ADDR") or username).strip()
    app_base = (
        os.environ.get("URGENT_EMAIL_APP_BASE_URL") or "https://produksi.eternalpackaging.id"
    ).strip()
    flag = (os.environ.get("URGENT_EMAIL_ENABLED") or "0").strip().lower()
    enabled_flag = flag in ("1", "true", "yes", "on")
    use_ssl = port == 465
    configured = bool(enabled_flag and host and username and password and from_addr)
    return SmtpConfig(
        enabled=configured,
        host=host,
        port=port,
        username=username,
        password=password,
        from_name=from_name,
        from_addr=from_addr,
        use_ssl=use_ssl,
        app_base_url=app_base,
    )


class SmtpSender(Protocol):
    def send(
        self,
        *,
        to_addr: str,
        subject: str,
        text: str,
        html: str,
    ) -> None: ...


class StdlibSmtpSender:
    def __init__(self, config: Optional[SmtpConfig] = None):
        self.config = config or load_smtp_config()

    def send(self, *, to_addr: str, subject: str, text: str, html: str) -> None:
        cfg = self.config
        if not cfg.enabled:
            raise RuntimeError("smtp_disabled_or_unconfigured")
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"{cfg.from_name} <{cfg.from_addr}>"
        msg["To"] = to_addr
        msg.attach(MIMEText(text or "", "plain", "utf-8"))
        msg.attach(MIMEText(html or "", "html", "utf-8"))
        timeout = float(os.environ.get("URGENT_EMAIL_SMTP_TIMEOUT_SEC") or "20")
        if cfg.use_ssl:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(cfg.host, cfg.port, timeout=timeout, context=context) as server:
                server.login(cfg.username, cfg.password)
                server.sendmail(cfg.from_addr, [to_addr], msg.as_string())
        else:
            with smtplib.SMTP(cfg.host, cfg.port, timeout=timeout) as server:
                server.ehlo()
                server.starttls(context=ssl.create_default_context())
                server.ehlo()
                server.login(cfg.username, cfg.password)
                server.sendmail(cfg.from_addr, [to_addr], msg.as_string())


class RecordingSmtpSender:
    """Test double — never opens a network connection."""

    def __init__(self, *, fail_times: int = 0, fail_forever: bool = False):
        self.sent = []
        self.fail_times = fail_times
        self.fail_forever = fail_forever
        self.attempts = 0

    def send(self, *, to_addr: str, subject: str, text: str, html: str) -> None:
        self.attempts += 1
        if self.fail_forever or self.attempts <= self.fail_times:
            raise RuntimeError("smtp_mock_failure")
        self.sent.append(
            {"to": to_addr, "subject": subject, "text": text, "html": html}
        )
