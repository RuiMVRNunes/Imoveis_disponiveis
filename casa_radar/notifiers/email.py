"""Email channel: Gmail SMTP with App Password (multipart HTML + text)."""

from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from ..core.config import ChannelConfig
from .base import Notifier, NotifyError

log = logging.getLogger("casa_radar.notifiers.email")


class EmailNotifier(Notifier):
    name = "email"

    def __init__(self, channel: ChannelConfig) -> None:
        self.channel = channel
        # GitHub Actions maps missing secrets to EMPTY strings, not unset
        # vars - "or" fallbacks (never dict defaults) keep that survivable.
        self.host = os.environ.get("SMTP_HOST") or "smtp.gmail.com"
        try:
            self.port = int(os.environ.get("SMTP_PORT") or "465")
        except ValueError:
            log.warning("email: SMTP_PORT inválido (%r); a usar 465", os.environ["SMTP_PORT"])
            self.port = 465
        self.user = os.environ.get("SMTP_USER") or ""
        self.password = os.environ.get("SMTP_PASS") or ""
        # The EMAIL_TO secret wins over config.yaml (secrets never in YAML).
        self.to = os.environ.get("EMAIL_TO") or channel.to

    def is_enabled(self) -> bool:
        if not self.channel.enabled:
            return False
        missing = [
            name
            for name, value in (
                ("SMTP_USER", self.user),
                ("SMTP_PASS", self.password),
                ("EMAIL_TO/notifications.email.to", self.to),
            )
            if not value
        ]
        if missing:
            log.warning("email: ativo no config mas faltam segredos: %s", ", ".join(missing))
            return False
        return True

    def send(self, subject: str, text: str, html: str | None = None) -> None:
        message = MIMEMultipart("alternative")
        message["Subject"] = subject
        message["From"] = self.user
        message["To"] = self.to
        message.attach(MIMEText(text, "plain", "utf-8"))
        if html:
            message.attach(MIMEText(html, "html", "utf-8"))
        try:
            if self.port == 465:
                with smtplib.SMTP_SSL(
                    self.host, self.port, context=ssl.create_default_context(), timeout=30
                ) as server:
                    server.login(self.user, self.password)
                    server.sendmail(self.user, [self.to], message.as_string())
            else:
                with smtplib.SMTP(self.host, self.port, timeout=30) as server:
                    server.starttls(context=ssl.create_default_context())
                    server.login(self.user, self.password)
                    server.sendmail(self.user, [self.to], message.as_string())
        except (smtplib.SMTPException, OSError) as exc:
            raise NotifyError(f"email: envio falhou: {exc}") from exc
