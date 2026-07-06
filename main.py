"""Casa Radar CLI.

Usage:
  python main.py                  # one run (default, cron-friendly)
  python main.py --dry-run        # show what would be notified; write nothing
  python main.py --baseline      # rebuild the baseline (no listing alerts)
  python main.py --test-notify    # send a test message on every active channel
  python main.py --source idealista   # run a single source
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from casa_radar.core.config import load_config
from casa_radar.core.runner import run_once
from casa_radar.core.state import State
from casa_radar.notifiers import build_notifiers
from casa_radar.notifiers.base import NotifyError
from casa_radar.notifiers.messages import build_test_message

log = logging.getLogger("casa_radar")


def _setup_logging(level_name: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level_name.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )


def _test_notify(config) -> int:
    notifiers = build_notifiers(config)
    if not notifiers:
        print("Nenhum canal ativo — vê o config.yaml e as variáveis de ambiente (.env.example).")
        return 1
    subject, text, html = build_test_message()
    failed = 0
    for notifier in notifiers:
        try:
            notifier.send(subject, text, html)
            print(f"[OK] {notifier.name}: mensagem de teste enviada")
        except NotifyError as exc:
            failed += 1
            print(f"[ERRO] {notifier.name}: {exc}")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="casa-radar", description="Radar pessoal de imóveis")
    parser.add_argument("--once", action="store_true", help="uma corrida e sai (default)")
    parser.add_argument("--dry-run", action="store_true", help="mostra o que notificaria; não envia nem grava")
    parser.add_argument("--baseline", action="store_true", help="reconstrói o baseline (regista tudo, sem alertas)")
    parser.add_argument("--test-notify", action="store_true", help="envia mensagem de teste por todos os canais ativos")
    parser.add_argument("--source", default=None, help="corre só uma fonte (ex: idealista)")
    parser.add_argument("--config", default="config.yaml", help="caminho do config.yaml")
    parser.add_argument("--state", default="state.json", help="caminho do state.json")
    parser.add_argument("--docs", default="docs", help="pasta do dashboard gerado")
    parser.add_argument(
        "--log-level",
        default=os.environ.get("LOG_LEVEL", "INFO"),
        help="DEBUG | INFO | WARNING | ERROR",
    )
    args = parser.parse_args(argv)
    _setup_logging(args.log_level)

    config = load_config(args.config)
    for error in config.errors:
        log.warning("config: %s", error)

    if args.test_notify:
        return _test_notify(config)

    if not config.searches:
        log.error("Sem pesquisas válidas no config.yaml — nada para fazer.")
        return 1

    state = State(args.state)
    result = run_once(
        config,
        state,
        dry_run=args.dry_run,
        only_source=args.source,
        force_baseline=args.baseline,
        dashboard_dir=args.docs,
    )
    # Exit 0 even when some sources failed: partial data beats a dead cron.
    log.info(
        "Feito: %d novos, %d baixas de preço, fontes com erro: %s",
        len(result.new_events),
        len(result.drop_events),
        list(result.errors_by_source) or "nenhuma",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
