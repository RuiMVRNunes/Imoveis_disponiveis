"""WhatsApp dashboard-stripping, Telegram multi-recipient, and source-date."""

from __future__ import annotations

import httpx
import pytest

from casa_radar.core.config import ChannelConfig
from casa_radar.core.utils import fmt_source_date
from casa_radar.notifiers.telegram import TelegramNotifier
from casa_radar.notifiers.whatsapp import WhatsAppNotifier


def test_fmt_source_date():
    assert fmt_source_date("2026-07-04 16:20:11") == "04/07"
    assert fmt_source_date("2026-07-04T16:20:11") == "04/07"
    assert fmt_source_date("2026-07-04") == "04/07"
    assert fmt_source_date(None) is None
    assert fmt_source_date("ontem") is None


def test_whatsapp_strips_dashboard_line(monkeypatch):
    monkeypatch.setenv("CALLMEBOT_PHONE", "+351900000000")
    monkeypatch.setenv("CALLMEBOT_APIKEY", "123")
    sent = {}

    def fake_get(url, params, timeout):
        sent["text"] = params["text"]
        return httpx.Response(200, text="Message queued.", request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", fake_get)
    WhatsAppNotifier(ChannelConfig(enabled=True)).send(
        "🏠 Novo", "• Moradia T3 — 320.000 €\n  https://x.pt/1\n\nDashboard: https://site/"
    )
    assert "Dashboard:" not in sent["text"]
    assert "Moradia T3" in sent["text"]


def test_telegram_sends_to_every_chat_id(monkeypatch):
    monkeypatch.setenv("TELEGRAM_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "111, 222")  # me + wife
    calls = []

    def fake_post(url, json, timeout):
        calls.append(json["chat_id"])
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(httpx, "post", fake_post)
    notifier = TelegramNotifier(ChannelConfig(enabled=True))
    assert notifier.is_enabled()
    notifier.send("Novo", "texto")
    assert calls == ["111", "222"]


def test_telegram_one_bad_id_does_not_block_the_other(monkeypatch):
    monkeypatch.setenv("TELEGRAM_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "111,222")

    def fake_post(url, json, timeout):
        ok = json["chat_id"] == "222"
        return httpx.Response(200, json={"ok": ok, "description": "bad"})

    monkeypatch.setattr(httpx, "post", fake_post)
    # 111 fails, 222 succeeds -> must NOT raise (one recipient still got it)
    TelegramNotifier(ChannelConfig(enabled=True)).send("Novo", "texto")
