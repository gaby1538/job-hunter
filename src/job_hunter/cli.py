"""Command line interface.

  python -m job_hunter extract [--sources email,adzuna] [--eml-dir DIR] [--out data]
  python -m job_hunter parse-eml FILE.eml [FILE2.eml ...]
  python -m job_hunter check-config
"""

from __future__ import annotations

import argparse
import email
import logging
import sys
from email import policy
from pathlib import Path

from job_hunter.config import ConfigError, load_config
from job_hunter.pipeline import build_sources, export, run
from job_hunter.settings import get_settings
from job_hunter.sources.email_parser import identify_site, parse_alert_email


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def cmd_extract(args: argparse.Namespace) -> int:
    settings = get_settings()
    config = load_config(Path(args.config or settings.job_hunter_config_dir))
    only = set(args.sources.split(",")) if args.sources else None
    eml_dir = Path(args.eml_dir) if args.eml_dir else None
    sources = build_sources(config, settings, only=only, eml_dir=eml_dir)
    if not sources:
        print("No source enabled. Check sources.yaml or --sources.", file=sys.stderr)
        return 1
    result = run(config, sources)
    run_dir = export(result, Path(args.out))
    print(f"Kept {len(result.kept)} jobs ({len(result.rejected)} filtered out, "
          f"{result.duplicates_merged} duplicates merged) -> {run_dir}")
    for name, err in result.errors.items():
        print(f"  ! {name}: {err}", file=sys.stderr)
    return 0 if len(result.errors) < len(sources) else 2


def cmd_parse_eml(args: argparse.Namespace) -> int:
    """Debug helper: show what the parser extracts from saved alert emails."""
    settings = get_settings()
    config = load_config(Path(args.config or settings.job_hunter_config_dir))
    for file in args.files:
        with open(file, "rb") as fh:
            msg = email.message_from_binary_file(fh, policy=policy.default)
        site = identify_site(msg, config.sources.email)
        print(f"\n== {file}  From: {msg.get('From')}")
        if site is None:
            print("   No site matched the sender. Add it to sources.yaml > email > sites.")
            continue
        jobs = parse_alert_email(msg, config.sources.email)
        print(f"   Site: {site[0]}  Jobs found: {len(jobs)}")
        for job in jobs:
            print(f"   - {job.title}")
            print(f"     url: {job.url}")
            if job.source_job_id:
                print(f"     id: {job.source_job_id}")
            print(f"     context lines: {job.raw.get('context')}")
    return 0


def cmd_check_config(args: argparse.Namespace) -> int:
    settings = get_settings()
    config = load_config(Path(args.config or settings.job_hunter_config_dir))
    s = config.sources
    print("Config OK")
    print(f"  queries: {config.search.queries}")
    print(f"  email: {'on' if s.email.enabled else 'off'} ({len(s.email.sites)} sites)")
    print(f"  adzuna: {'on' if s.adzuna.enabled else 'off'}"
          f" (keys {'set' if settings.adzuna_app_id else 'missing'})")
    ft = s.france_travail
    print(f"  france_travail: {'on' if ft and ft.enabled else 'off'}")
    print(f"  rss: {'on' if s.rss.enabled else 'off'} ({len(s.rss.feeds)} feeds)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="job_hunter")
    parser.add_argument("--config", help="config directory (default: JOB_HUNTER_CONFIG_DIR)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("extract", help="collect jobs from all enabled sources")
    p.add_argument("--sources", help="comma list: email,adzuna,france_travail,rss")
    p.add_argument("--eml-dir", help="read alert emails from .eml files instead of IMAP")
    p.add_argument("--out", default="data/runs")
    p.set_defaults(func=cmd_extract)

    p = sub.add_parser("parse-eml", help="debug the email parser on saved .eml files")
    p.add_argument("files", nargs="+")
    p.set_defaults(func=cmd_parse_eml)

    p = sub.add_parser("check-config", help="validate YAML parameters")
    p.set_defaults(func=cmd_check_config)

    args = parser.parse_args(argv)
    _setup_logging(get_settings().log_level)
    try:
        return int(args.func(args))
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 1
