"""Message composition (Portuguese, per spec: user-facing text is PT).

Every builder returns (subject, text, html|None): the plain text feeds
WhatsApp/Telegram and the email fallback; the HTML feeds the email cards.
"""

from __future__ import annotations

import html as html_lib
from collections import defaultdict
from typing import Any

from ..core.utils import fmt_price

SOURCE_BADGE_COLORS = {
    "idealista": "#c9f24d",
    "idealista_api": "#a0c400",
    "imovirtual": "#0059b3",
    "supercasa": "#e8452c",
    "custojusto": "#7b2d8b",
    "casasapo": "#00a651",
}


def _specs(event: dict[str, Any]) -> str:
    parts = []
    if event.get("rooms") is not None:
        parts.append(f"T{event['rooms']}")
    if event.get("area_m2"):
        parts.append(f"{event['area_m2']:.0f} m²")
    if event.get("location"):
        parts.append(str(event["location"]))
    return " · ".join(parts)


def _event_line(event: dict[str, Any]) -> str:
    price = fmt_price(event.get("price"))
    if event.get("type") == "price_drop" and event.get("old_price"):
        price = f"{fmt_price(event['old_price'])} ➜ {price}"
    specs = _specs(event)
    line = f"• {event.get('title', 'Sem título')} — {price}"
    if specs:
        line += f"\n  {specs}"
    line += f" ({event.get('source', '?')})\n  {event.get('url', '')}"
    return line


def build_new_single(event: dict[str, Any], dashboard_url: str = "") -> tuple[str, str, str]:
    kind = "Baixa de preço" if event.get("type") == "price_drop" else "Novo"
    subject = f"🏠 {kind} em \"{event.get('search_name', '')}\": {fmt_price(event.get('price'))}"
    text = _event_line(event)
    if dashboard_url:
        text += f"\n\nDashboard: {dashboard_url}"
    html = _email_html(
        f"{kind} anúncio — pesquisa \"{event.get('search_name', '')}\"",
        [event],
        dashboard_url,
    )
    return subject, text, html


def build_run_digest(
    new_events: list[dict[str, Any]],
    drop_events: list[dict[str, Any]],
    dashboard_url: str = "",
    max_listings: int = 20,
) -> tuple[str, str, str]:
    by_search: dict[str, int] = defaultdict(int)
    for event in new_events:
        by_search[event.get("search_name", "?")] += 1
    summary_parts = [f"{count} novos em \"{name}\"" for name, count in by_search.items()]
    if drop_events:
        summary_parts.append(f"{len(drop_events)} baixas de preço")
    subject = "🏠 Casa Radar: " + ", ".join(summary_parts)

    lines = []
    shown = 0
    for name in by_search:
        lines.append(f"— Pesquisa \"{name}\" —")
        for event in new_events:
            if event.get("search_name") != name:
                continue
            if shown >= max_listings:
                break
            lines.append(_event_line(event))
            shown += 1
    if drop_events:
        lines.append("— Baixas de preço —")
        for event in drop_events[: max(0, max_listings - shown)]:
            lines.append(_event_line(event))
    hidden = len(new_events) + len(drop_events) - min(len(new_events) + len(drop_events), max_listings)
    if hidden > 0:
        lines.append(f"… e mais {hidden} — vê tudo no dashboard.")
    if dashboard_url:
        lines.append(f"\nDashboard: {dashboard_url}")
    text = "\n\n".join(lines)

    html = _email_html(subject.removeprefix("🏠 "), (new_events + drop_events)[:max_listings], dashboard_url)
    return subject, text, html


def build_baseline_message(
    per_search: dict[str, int], sources_ok: int, sources_total: int
) -> tuple[str, str, None]:
    total = sum(per_search.values())
    subject = "✅ Casa Radar ativo — baseline criado"
    text = (
        f"Casa Radar ativo ✅ — baseline criado: {total} anúncios registados em "
        f"{len(per_search)} pesquisa(s), {sources_ok}/{sources_total} fontes OK. "
        "A partir daqui só recebes o que for novo."
    )
    if per_search:
        text += "\n" + "\n".join(f"• {name}: {count} anúncios" for name, count in per_search.items())
    return subject, text, None


def build_block_alert(source: str, hours: int) -> tuple[str, str, None]:
    subject = f"⚠️ Casa Radar: {source} sem resultados"
    text = (
        f"⚠️ {source}: 0 resultados há {hours}h, provável bloqueio ou mudança no site. "
        "Vê os logs da última corrida no separador Actions do GitHub."
    )
    return subject, text, None


def build_heartbeat(
    day_label: str,
    runs_today: int,
    source_status: dict[str, str],
    new_today: int,
    drops_today: int,
    config_errors: list[str],
    dashboard_url: str = "",
    removed_events: list[dict[str, Any]] | None = None,
) -> tuple[str, str, None]:
    subject = f"📊 Casa Radar — resumo de {day_label}"
    status = " · ".join(f"{name} {icon}" for name, icon in source_status.items()) or "sem fontes"
    text = (
        f"{runs_today} corridas hoje · {status}\n"
        f"Novos: {new_today} · Baixas de preço: {drops_today}"
    )
    if new_today == 0 and drops_today == 0:
        text += "\nDia calmo — nada de novo, mas o radar esteve a postos."
    if removed_events:
        text += f"\n\n🚪 Desapareceram do mercado ({len(removed_events)}):"
        for event in removed_events[:5]:
            days = event.get("days_on_market")
            days_note = f" — esteve {days} dia(s) no mercado" if days is not None else ""
            text += f"\n• {event.get('title', '?')} ({fmt_price(event.get('price'))}){days_note}"
        if len(removed_events) > 5:
            text += f"\n• … e mais {len(removed_events) - 5}"
    if config_errors:
        text += "\n\n⚠️ Problemas no config.yaml:\n" + "\n".join(f"• {e}" for e in config_errors)
    if dashboard_url:
        text += f"\n\nDashboard: {dashboard_url}"
    return subject, text, None


def build_test_message() -> tuple[str, str, None]:
    return (
        "🔔 Casa Radar — teste",
        "Mensagem de teste do Casa Radar. Se estás a ler isto, este canal está bem configurado. ✅",
        None,
    )


# -- email HTML -----------------------------------------------------------------


def _card(event: dict[str, Any]) -> str:
    esc = html_lib.escape
    badge_color = SOURCE_BADGE_COLORS.get(event.get("source", ""), "#666")
    price = fmt_price(event.get("price"))
    price_html = esc(price)
    tag = ""
    if event.get("type") == "price_drop":
        old = fmt_price(event.get("old_price"))
        price_html = (
            f"<span style='text-decoration:line-through;color:#999;font-size:14px'>{esc(old)}</span> "
            f"<span style='color:#188038'>{esc(price)}</span>"
        )
        tag = "<span style='background:#188038;color:#fff;border-radius:4px;padding:2px 8px;font-size:12px'>baixa de preço</span>"
    else:
        tag = "<span style='background:#1a73e8;color:#fff;border-radius:4px;padding:2px 8px;font-size:12px'>novo</span>"
    image = ""
    if event.get("image_url"):
        image = (
            f"<img src='{esc(event['image_url'])}' alt='' width='560' "
            "style='width:100%;max-width:560px;border-radius:8px 8px 0 0;display:block;object-fit:cover;max-height:260px'>"
        )
    specs = esc(_specs(event))
    return f"""
    <div style="border:1px solid #e0e0e0;border-radius:8px;margin:0 0 16px 0;overflow:hidden;background:#fff">
      {image}
      <div style="padding:14px 16px">
        <div style="margin-bottom:6px">{tag}
          <span style="background:{badge_color};color:#fff;border-radius:4px;padding:2px 8px;font-size:12px">{esc(event.get('source', ''))}</span>
        </div>
        <div style="font-size:17px;font-weight:600;margin:2px 0">{esc(event.get('title', 'Sem título'))}</div>
        <div style="font-size:20px;font-weight:700;margin:4px 0">{price_html}</div>
        <div style="color:#5f6368;font-size:14px;margin-bottom:12px">{specs}</div>
        <a href="{esc(event.get('url', '#'))}"
           style="background:#1a73e8;color:#fff;text-decoration:none;padding:9px 18px;border-radius:6px;font-size:14px;display:inline-block">Ver anúncio</a>
      </div>
    </div>"""


def _email_html(heading: str, events: list[dict[str, Any]], dashboard_url: str = "") -> str:
    cards = "\n".join(_card(e) for e in events)
    footer = ""
    if dashboard_url:
        footer = (
            f"<p style='text-align:center'><a href='{html_lib.escape(dashboard_url)}' "
            "style='color:#1a73e8'>Abrir o dashboard</a></p>"
        )
    return f"""<!DOCTYPE html>
<html lang="pt"><body style="margin:0;padding:24px 12px;background:#f5f5f5;font-family:-apple-system,'Segoe UI',Roboto,Arial,sans-serif">
  <div style="max-width:600px;margin:0 auto">
    <h2 style="font-size:18px;color:#202124">🏠 {html_lib.escape(heading)}</h2>
    {cards}
    {footer}
    <p style="color:#9aa0a6;font-size:12px;text-align:center">Casa Radar — radar pessoal de imóveis</p>
  </div>
</body></html>"""
