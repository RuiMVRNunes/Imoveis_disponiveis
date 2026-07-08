"""Static dashboard generator -> docs/index.html (GitHub Pages).

Read-only, as fresh as the last run. No JS frameworks, no external assets:
one self-contained HTML file with inline CSS and a pure-CSS bar chart.
User-facing text in Portuguese (per spec).
"""

from __future__ import annotations

import html as html_lib
from datetime import datetime, timedelta
from pathlib import Path

from ..core.config import AppConfig
from ..core.state import State, _parse_iso
from ..core.utils import fmt_price

_ACCENT = "#1a5fb4"
_OK = "#2e7d32"
_WARN = "#b45309"


def generate(
    state: State, config: AppConfig, now: datetime, out_dir: str | Path = "docs"
) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / ".nojekyll").touch()
    html = render(state, config, now)
    out_path = out_dir / "index.html"
    out_path.write_text(html, encoding="utf-8")
    return out_path


def _tracked_inventory(state: State, now: datetime) -> list[dict]:
    """Active listings currently being tracked: seen after the last baseline
    (keeps junk registered by old configs/parsers out) and not flagged as
    removed. Newest first."""
    baseline_at = _parse_iso(state.data["meta"].get("last_baseline_at"))
    cutoff = max(baseline_at, now - timedelta(hours=48))
    items = [
        entry
        for entry in state.listings.values()
        if "alias_of" not in entry
        and not entry.get("removed_at")
        and _parse_iso(entry.get("last_seen") or entry.get("first_seen")) >= cutoff
    ]
    items.sort(key=lambda e: str(e.get("first_seen") or ""), reverse=True)
    return items


def render(state: State, config: AppConfig, now: datetime) -> str:
    esc = html_lib.escape
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    runs_today = state.runs_since(day_start)
    events_today = state.events_since(day_start)
    events_24h = state.events_since(now - timedelta(hours=24))
    last_run = state.data["runs"][-1] if state.data["runs"] else None

    seen_today = sum(sum(r.get("seen", {}).values()) for r in runs_today)
    new_today = sum(1 for e in events_today if e.get("type") == "new")
    drops_today = sum(1 for e in events_today if e.get("type") == "price_drop")
    events_30d = state.events_since(now - timedelta(days=30))
    new_30d = sum(1 for e in events_30d if e.get("type") == "new")
    inventory = _tracked_inventory(state, now)

    # -- top status ---------------------------------------------------------
    if last_run:
        last_at = _parse_iso(last_run["at"]).astimezone(now.tzinfo)
        next_at = last_at + timedelta(hours=1)
        status_line = (
            f"Ativo · última corrida {last_at.strftime('%d/%m %H:%M')} · "
            f"próxima ~{next_at.strftime('%H:%M')}"
        )
    else:
        status_line = "À espera da primeira corrida…"

    # -- source cards -------------------------------------------------------
    threshold = config.runtime.silent_block_threshold
    source_cards = []
    for name, health in sorted(state.data["sources"].items()):
        streak = int(health.get("zero_streak", 0))
        blocked = streak >= threshold
        color = _WARN if blocked else _OK
        label = f"0 há {streak}h" if blocked else "OK"
        detail = ""
        if last_run:
            detail = f"{last_run.get('seen', {}).get(name, 0)} vistos na última corrida"
        if blocked:
            detail = "provável bloqueio ou mudança no site"
        source_cards.append(
            f"""<div class="scard" style="border-left:4px solid {color}">
              <div class="sname">{esc(name)}</div>
              <div class="sstatus" style="color:{color}">{'⚠️' if blocked else '●'} {esc(label)}</div>
              <div class="sdetail">{esc(detail)}</div>
            </div>"""
        )
    sources_html = "\n".join(source_cards) or "<p class='muted'>Sem dados de fontes ainda.</p>"

    # -- 24h activity chart (pure CSS bars) ---------------------------------
    buckets = [0] * 24
    hour_labels = []
    start_hour = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=23)
    for i in range(24):
        hour_labels.append((start_hour + timedelta(hours=i)).strftime("%Hh"))
    for event in events_24h:
        if event.get("type") != "new":
            continue
        delta = _parse_iso(event["at"]).astimezone(now.tzinfo) - start_hour
        index = int(delta.total_seconds() // 3600)
        if 0 <= index < 24:
            buckets[index] += 1
    peak = max(buckets) or 1
    bars = []
    for i, count in enumerate(buckets):
        height = max(4, round(count / peak * 100)) if count else 4
        fill = _ACCENT if count else "#d5dbe3"
        bars.append(
            f"""<div class="bar-slot" title="{hour_labels[i]}: {count} novo(s)">
              <div class="bar" style="height:{height}%;background:{fill}"></div>
              <div class="bar-label">{hour_labels[i] if i % 4 == 3 else ''}</div>
            </div>"""
        )
    chart_html = "\n".join(bars)

    # -- today's listings ---------------------------------------------------
    cards = []
    for event in sorted(events_today, key=lambda e: e.get("at", ""), reverse=True):
        minutes = max(0, int((now - _parse_iso(event["at"]).astimezone(now.tzinfo)).total_seconds() // 60))
        ago = f"há {minutes} min" if minutes < 60 else f"há {minutes // 60}h{minutes % 60:02d}"
        is_drop = event.get("type") == "price_drop"
        badge = (
            f"<span class='badge' style='background:{_OK}'>baixa de preço</span>"
            if is_drop
            else f"<span class='badge' style='background:{_ACCENT}'>novo</span>"
        )
        price = fmt_price(event.get("price"))
        price_html = esc(price)
        if is_drop and event.get("old_price"):
            price_html = f"<s class='muted'>{esc(fmt_price(event['old_price']))}</s> {esc(price)}"
        specs = " · ".join(
            part
            for part in (
                f"T{event['rooms']}" if event.get("rooms") is not None else "",
                f"{event['area_m2']:.0f} m²" if event.get("area_m2") else "",
                esc(str(event.get("location") or "")),
            )
            if part
        )
        image = (
            f"<div class='thumb' style=\"background-image:url('{esc(event['image_url'])}')\"></div>"
            if event.get("image_url")
            else "<div class='thumb thumb-empty'>🏠</div>"
        )
        cards.append(
            f"""<a class="card" href="{esc(event.get('url', '#'))}" target="_blank" rel="noopener">
              {image}
              <div class="card-body">
                <div>{badge} <span class="badge badge-src">{esc(event.get('source', ''))}</span>
                     <span class="muted small">{ago}</span></div>
                <div class="card-title">{esc(event.get('title', 'Sem título'))}</div>
                <div class="card-price">{price_html}</div>
                <div class="muted small">{specs}</div>
                <div class="muted small">pesquisa: {esc(event.get('search_name', ''))}</div>
              </div>
            </a>"""
        )
    cards_html = (
        "\n".join(cards)
        if cards
        else "<p class='muted'>Nada de novo hoje — o radar continua atento. 🎯</p>"
    )

    # -- tracked inventory ("what is on the market right now") ---------------
    inventory_cards = []
    for entry in inventory[:60]:
        price_html = esc(fmt_price(entry.get("last_price")))
        specs = " · ".join(
            part
            for part in (
                f"T{entry['rooms']}" if entry.get("rooms") is not None else "",
                f"{entry['area_m2']:.0f} m²" if entry.get("area_m2") else "",
                esc(str(entry.get("location") or "")),
            )
            if part
        )
        since = _parse_iso(entry.get("first_seen")).astimezone(now.tzinfo)
        image = (
            f"<div class='thumb' style=\"background-image:url('{esc(entry['image_url'])}')\"></div>"
            if entry.get("image_url")
            else "<div class='thumb thumb-empty'>🏠</div>"
        )
        url = (entry.get("urls") or ["#"])[0]
        extra_urls = ""
        if len(entry.get("urls") or []) > 1:
            extra_urls = f"<span class='muted small'>+{len(entry['urls']) - 1} portal(is)</span>"
        inventory_cards.append(
            f"""<a class="card" href="{esc(url)}" target="_blank" rel="noopener">
              {image}
              <div class="card-body">
                <div><span class="badge badge-src">{esc(entry.get('source', ''))}</span>
                     <span class="muted small">no radar desde {since.strftime('%d/%m')}</span> {extra_urls}</div>
                <div class="card-title">{esc(entry.get('title', 'Sem título'))}</div>
                <div class="card-price">{price_html}</div>
                <div class="muted small">{specs}</div>
                <div class="muted small">pesquisa: {esc(entry.get('search_name', ''))}</div>
              </div>
            </a>"""
        )
    inventory_html = (
        "\n".join(inventory_cards)
        if inventory_cards
        else "<p class='muted'>Ainda sem inventário — aparece depois da próxima corrida.</p>"
    )
    inventory_note = (
        f"<p class='muted small'>{len(inventory)} imóveis em seguimento"
        + (" (a mostrar os 60 mais recentes)" if len(inventory) > 60 else "")
        + " — vistos nas fontes nas últimas 48h, ordenados do mais recente.</p>"
    )

    generated = now.strftime("%d/%m/%Y %H:%M")
    return f"""<!DOCTYPE html>
<html lang="pt">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="900">
<title>Casa Radar</title>
<style>
  :root {{ color-scheme: light; }}
  * {{ box-sizing: border-box; margin: 0; }}
  body {{
    font-family: -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    background: #f2f4f7; color: #1c2430; line-height: 1.45;
    padding: 20px 14px 48px; max-width: 1080px; margin: 0 auto;
  }}
  h1 {{ font-size: 1.5rem; letter-spacing: -0.02em; }}
  h2 {{ font-size: 1.02rem; margin: 30px 0 12px; color: #46536a;
       text-transform: uppercase; letter-spacing: 0.06em; font-weight: 600; }}
  .muted {{ color: #6b7a90; }}
  .small {{ font-size: 0.8rem; }}
  .status {{ color: #46536a; margin-top: 4px; }}
  .dot {{ color: {_OK}; }}
  .metrics {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
              gap: 12px; margin-top: 16px; }}
  .metric {{ background: #fff; border: 1px solid #e3e8ef; border-radius: 10px;
             padding: 14px 16px; }}
  .metric b {{ display: block; font-size: 1.7rem; font-variant-numeric: tabular-nums;
               letter-spacing: -0.02em; }}
  .metric span {{ color: #6b7a90; font-size: 0.82rem; }}
  .sources {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
              gap: 12px; }}
  .scard {{ background: #fff; border: 1px solid #e3e8ef; border-radius: 10px;
            padding: 12px 14px; }}
  .sname {{ font-weight: 600; }}
  .sstatus {{ font-size: 0.86rem; font-weight: 600; margin: 2px 0; }}
  .sdetail {{ color: #6b7a90; font-size: 0.8rem; }}
  .chart {{ display: flex; align-items: flex-end; gap: 3px; height: 120px;
            background: #fff; border: 1px solid #e3e8ef; border-radius: 10px;
            padding: 14px 14px 26px; }}
  .bar-slot {{ flex: 1; height: 100%; position: relative; display: flex;
               flex-direction: column; justify-content: flex-end; }}
  .bar {{ width: 100%; border-radius: 3px 3px 0 0; min-height: 3px; }}
  .bar-label {{ position: absolute; bottom: -20px; left: 50%; transform: translateX(-50%);
                font-size: 0.62rem; color: #8b98ab; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(250px, 1fr));
           gap: 14px; }}
  .card {{ background: #fff; border: 1px solid #e3e8ef; border-radius: 10px;
           overflow: hidden; text-decoration: none; color: inherit; display: block;
           transition: box-shadow 0.15s ease, transform 0.15s ease; }}
  .card:hover {{ box-shadow: 0 4px 16px rgba(28, 36, 48, 0.12); transform: translateY(-2px); }}
  .thumb {{ height: 150px; background-size: cover; background-position: center; }}
  .thumb-empty {{ display: flex; align-items: center; justify-content: center;
                  font-size: 2rem; background: #e8edf3; }}
  .card-body {{ padding: 12px 14px 14px; display: grid; gap: 4px; }}
  .card-title {{ font-weight: 600; font-size: 0.95rem; }}
  .card-price {{ font-size: 1.15rem; font-weight: 700; font-variant-numeric: tabular-nums; }}
  .badge {{ color: #fff; border-radius: 4px; padding: 2px 7px; font-size: 0.7rem;
            font-weight: 600; }}
  .badge-src {{ background: #46536a; }}
  footer {{ margin-top: 40px; color: #8b98ab; font-size: 0.78rem; text-align: center; }}
</style>
</head>
<body>
  <h1>🏠 Casa Radar</h1>
  <p class="status"><span class="dot">●</span> {esc(status_line)}</p>

  <div class="metrics">
    <div class="metric"><b>{len(runs_today)}</b><span>corridas hoje</span></div>
    <div class="metric"><b>{seen_today}</b><span>anúncios vistos hoje</span></div>
    <div class="metric"><b>{new_today}</b><span>novos hoje</span></div>
    <div class="metric"><b>{drops_today}</b><span>baixas de preço hoje</span></div>
    <div class="metric"><b>{new_30d}</b><span>novos (30 dias)</span></div>
    <div class="metric"><b>{len(inventory)}</b><span>em seguimento</span></div>
  </div>

  <h2>Estado das fontes</h2>
  <div class="sources">{sources_html}</div>

  <h2>Atividade — novos por hora (24h)</h2>
  <div class="chart">{chart_html}</div>

  <h2>Apareceu hoje</h2>
  <div class="grid">{cards_html}</div>

  <h2>No mercado agora</h2>
  {inventory_note}
  <div class="grid">{inventory_html}</div>

  <footer>Gerado pelo Casa Radar às {esc(generated)} ({esc(config.runtime.timezone)}) ·
    atualiza a cada corrida (~1h)</footer>
</body>
</html>"""
