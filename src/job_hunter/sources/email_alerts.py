"""Email-alert source: reads alert emails from an IMAP inbox or from a folder of .eml files."""

from __future__ import annotations

import email
import imaplib
import logging
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from email import policy
from email.message import Message
from pathlib import Path

from job_hunter.capture import CaptureItem, is_capture_email, parse_capture_email
from job_hunter.config import CaptureConfig, EmailConfig, SearchCriteria
from job_hunter.models import JobPosting
from job_hunter.settings import Settings
from job_hunter.sources.base import SourceError
from job_hunter.sources.email_parser import identify_site, parse_alert_email

logger = logging.getLogger(__name__)


def iter_eml_dir(folder: Path) -> Iterator[Message]:
    for path in sorted(folder.glob("*.eml")):
        with path.open("rb") as fh:
            yield email.message_from_binary_file(fh, policy=policy.default)


def iter_imap(settings: Settings, cfg: EmailConfig) -> Iterator[Message]:
    if not settings.imap_user or not settings.imap_password:
        raise SourceError("IMAP_USER / IMAP_PASSWORD not set")
    since = (datetime.now(UTC) - timedelta(days=cfg.lookback_days)).strftime("%d-%b-%Y")
    try:
        conn = imaplib.IMAP4_SSL(settings.imap_host, settings.imap_port)
        conn.login(settings.imap_user, settings.imap_password.get_secret_value())
    except (imaplib.IMAP4.error, OSError) as exc:
        raise SourceError(f"IMAP login failed: {exc}") from exc
    try:
        status, _ = conn.select(cfg.mailbox, readonly=True)  # read-only: never alters the inbox
        if status != "OK":
            raise SourceError(f"cannot open mailbox {cfg.mailbox!r}")
        status, data = conn.search(None, "SINCE", since)
        if status != "OK":
            raise SourceError("IMAP search failed")
        for num in data[0].split():
            status, parts = conn.fetch(num, "(BODY.PEEK[])")
            if status != "OK" or not parts or not isinstance(parts[0], tuple):
                logger.warning("could not fetch message %s", num)
                continue
            yield email.message_from_bytes(parts[0][1], policy=policy.default)
    finally:
        try:
            conn.logout()
        except (imaplib.IMAP4.error, OSError):
            pass


class EmailAlertSource:
    name = "email"

    def __init__(
        self,
        cfg: EmailConfig,
        settings: Settings,
        eml_dir: Path | None = None,
        capture_cfg: CaptureConfig | None = None,
    ) -> None:
        self.cfg = cfg
        self.settings = settings
        self.eml_dir = eml_dir
        self.capture_cfg = capture_cfg or CaptureConfig()
        self.captures: list[CaptureItem] = []  # descriptions you emailed yourself

    def fetch(self, criteria: SearchCriteria) -> list[JobPosting]:
        messages = iter_eml_dir(self.eml_dir) if self.eml_dir else iter_imap(self.settings, self.cfg)
        jobs: list[JobPosting] = []
        seen = unknown = 0
        senders = self.settings.capture_sender_list
        self.captures = []
        for msg in messages:
            seen += 1
            if is_capture_email(msg, self.capture_cfg, senders):
                item = parse_capture_email(msg, self.capture_cfg)
                if item is not None:
                    self.captures.append(item)
                continue
            if identify_site(msg, self.cfg) is None:
                unknown += 1
                continue
            try:
                parsed = parse_alert_email(msg, self.cfg)
            except Exception:  # one malformed email must not stop the run
                logger.exception("failed to parse email %r", msg.get("Subject"))
                continue
            if not parsed:
                logger.warning(
                    "alert email from %s gave 0 jobs - check its job_url_patterns",
                    msg.get("From"),
                )
            jobs.extend(parsed)
        logger.info(
            "email: %d messages, %d from unknown senders, %d jobs, %d captured descriptions",
            seen, unknown, len(jobs), len(self.captures),
        )
        return jobs
