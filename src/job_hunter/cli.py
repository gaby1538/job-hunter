"""Command line interface.

  python -m job_hunter run      [--sources ...] [--eml-dir DIR] [--no-analysis]
      collect -> store -> attach captured descriptions -> analyse -> shortlist
  python -m job_hunter extract  [--sources ...] [--eml-dir DIR]   (run without analysis)
  python -m job_hunter capture  --url URL [--file FILE | stdin] [--title TITLE]
  python -m job_hunter shortlist                                   (rebuild the report)
  python -m job_hunter parse-eml FILE.eml [...]                    (debug the email parser)
  python -m job_hunter check-config
"""

from __future__ import annotations

import argparse
import email
import logging
import sys
from datetime import UTC, datetime
from email import policy
from pathlib import Path

from job_hunter.analysis.analyzer import Analyzer
from job_hunter.analysis.report import write_reports
from job_hunter.capture import CaptureItem, apply_capture
from job_hunter.config import ConfigError, load_config
from job_hunter.pipeline import build_sources, daily
from job_hunter.profile import load_profile
from job_hunter.settings import Settings, get_settings
from job_hunter.sources.email_parser import identify_site, parse_alert_email
from job_hunter.store import JobStore


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _config_dir(args: argparse.Namespace, settings: Settings) -> Path:
    return Path(args.config or settings.job_hunter_config_dir)


def _store(settings: Settings) -> JobStore:
    return JobStore(settings.job_hunter_data_dir / "jobs.json")


def cmd_run(args: argparse.Namespace) -> int:
    settings = get_settings()
    config_dir = _config_dir(args, settings)
    config, profile = load_config(config_dir), load_profile(config_dir)
    only = set(args.sources.split(",")) if args.sources else None
    eml_dir = Path(args.eml_dir) if args.eml_dir else None
    sources = build_sources(config, settings, only=only, eml_dir=eml_dir)
    if not sources:
        print("No source enabled. Check sources.yaml or --sources.", file=sys.stderr)
        return 1

    analyzer = None
    if not args.no_analysis:
        if settings.anthropic_api_key is None:
            print("ANTHROPIC_API_KEY not set: jobs are collected but not analysed.",
                  file=sys.stderr)
        else:
            analyzer = Analyzer.from_api_key(
                profile, settings.anthropic_api_key.get_secret_value()
            )

    store = _store(settings)
    res = daily(config, sources, store, analyzer, settings.job_hunter_data_dir / "runs",
                profile.analysis.max_jobs_per_run)
    ex = res.extraction
    print(f"Collected {sum(ex.per_source.values())}, kept {len(ex.kept)} "
          f"({len(ex.rejected)} filtered out), {res.new_jobs} new, "
          f"{res.captures_applied} captured descriptions attached.")
    if res.analysis:
        a = res.analysis
        print(f"Analysed {a.analysed} jobs ({a.failed} failed, {a.skipped_limit} left for "
              f"next run; {a.input_tokens} input / {a.output_tokens} output tokens).")
    print(f"Shortlist: {res.shortlist}")
    for name, err in ex.errors.items():
        print(f"  ! {name}: {err}", file=sys.stderr)
    return 0 if len(ex.errors) < len(sources) else 2


def cmd_extract(args: argparse.Namespace) -> int:
    args.no_analysis = True
    return cmd_run(args)


def cmd_capture(args: argparse.Namespace) -> int:
    settings = get_settings()
    config = load_config(_config_dir(args, settings))
    text = Path(args.file).read_text(encoding="utf-8") if args.file else sys.stdin.read()
    if len(text.strip()) < config.sources.capture.min_chars:
        print("Text too short to be a job description.", file=sys.stderr)
        return 1
    store = _store(settings)
    rec = apply_capture(store, CaptureItem(text=text, url=args.url, title_hint=args.title),
                        config.sources.email)
    store.save()
    print(f"Description attached to: {rec.job.title} ({rec.fingerprint}). "
          "It will be analysed on the next run.")
    return 0


def cmd_shortlist(args: argparse.Namespace) -> int:
    settings = get_settings()
    md, csv_path = write_reports(_store(settings), settings.job_hunter_data_dir / "reports",
                                 datetime.now(UTC))
    print(f"Shortlist: {md}\nTable: {csv_path}")
    return 0


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
            shown = {
                "url": job.url, "id": job.source_job_id, "company": job.company,
                "location": job.location, "contract": job.contract_type.value,
                "salary": job.salary_text, "remote": job.remote_text,
                "published": job.published_at.isoformat() if job.published_at else None,
            }
            for key, value in shown.items():
                if value and value != "unknown":
                    print(f"     {key}: {value}")
            if job.salary_may_be_estimate:
                print("     (salary may be the site's own estimate)")
            for key, value in job.raw.get("details", {}).items():
                if key != "contract":
                    print(f"     {key}: {value[:80]}")
            if job.raw.get("context"):
                print(f"     other lines: {job.raw['context']}")
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
    print(f"  employers: {'on' if s.employers.enabled else 'off'} "
          f"({len(s.employers.companies)} companies)")
    print(f"  capture: {'on' if s.capture.enabled else 'off'} "
          f"({len(settings.capture_sender_list)} allowed senders)")
    profile = load_profile(_config_dir(args, settings))
    print(f"  profile: {len(profile.skills)} skills, model {profile.analysis.model}, "
          f"API key {'set' if settings.anthropic_api_key else 'missing'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="job_hunter")
    parser.add_argument("--config", help="config directory (default: JOB_HUNTER_CONFIG_DIR)")
    sub = parser.add_subparsers(dest="command", required=True)

    for name, func, helptext in (
        ("run", cmd_run, "collect, analyse and build the shortlist"),
        ("extract", cmd_extract, "collect and store jobs only (no analysis)"),
    ):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("--sources", help="comma list: email,adzuna,employers,france_travail,rss")
        p.add_argument("--eml-dir", help="read alert emails from .eml files instead of IMAP")
        p.add_argument("--no-analysis", action="store_true")
        p.set_defaults(func=func)

    p = sub.add_parser("capture", help="attach a job description you copied")
    p.add_argument("--url", help="link of the job ad")
    p.add_argument("--file", help="text file with the description (default: stdin)")
    p.add_argument("--title", help="job title, if the job is not already stored")
    p.set_defaults(func=cmd_capture)

    p = sub.add_parser("shortlist", help="rebuild the shortlist report from stored analyses")
    p.set_defaults(func=cmd_shortlist)

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
