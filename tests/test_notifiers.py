"""Notifier construction must survive the GitHub Actions quirk of mapping
missing secrets to EMPTY environment variables (not unset ones)."""

from casa_radar.core.config import AppConfig, ChannelConfig, NotificationsConfig
from casa_radar.notifiers import build_notifiers
from casa_radar.notifiers.email import EmailNotifier


def _all_enabled_config() -> AppConfig:
    return AppConfig(
        notifications=NotificationsConfig(
            email=ChannelConfig(enabled=True, to="eu@exemplo.com"),
            whatsapp=ChannelConfig(enabled=True),
            telegram=ChannelConfig(enabled=True),
        )
    )


def _blank_secrets(monkeypatch) -> None:
    for name in (
        "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASS", "EMAIL_TO",
        "CALLMEBOT_PHONE", "CALLMEBOT_APIKEY", "TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID",
    ):
        monkeypatch.setenv(name, "")


def test_empty_secret_env_vars_never_crash(monkeypatch):
    _blank_secrets(monkeypatch)
    notifier = EmailNotifier(ChannelConfig(enabled=True))
    assert notifier.host == "smtp.gmail.com"
    assert notifier.port == 465
    assert notifier.is_enabled() is False  # no credentials -> inactive, not dead
    assert build_notifiers(_all_enabled_config()) == []


def test_invalid_smtp_port_falls_back(monkeypatch):
    _blank_secrets(monkeypatch)
    monkeypatch.setenv("SMTP_PORT", "quinhentos")
    assert EmailNotifier(ChannelConfig(enabled=True)).port == 465


def test_configured_email_channel_is_active(monkeypatch):
    _blank_secrets(monkeypatch)
    monkeypatch.setenv("SMTP_USER", "eu@gmail.com")
    monkeypatch.setenv("SMTP_PASS", "app-password")
    monkeypatch.setenv("EMAIL_TO", "eu@gmail.com")
    active = build_notifiers(_all_enabled_config())
    assert [n.name for n in active] == ["email"]
