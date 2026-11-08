"""Send the shortlist to you by email (SMTP), so results never need to be published."""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from pathlib import Path

from job_hunter.settings import Settings

logger = logging.getLogger(__name__)


class NotifyError(Exception):
    pass


def send_report(settings: Settings, subject: str, body: str, attachments: list[Path]) -> str:
    """Email the report to REPORT_TO (default: your IMAP address). Returns the recipient."""
    user = settings.smtp_user or settings.imap_user
    secret = settings.smtp_password or settings.imap_password
    to = settings.report_to or user
    if not user or not secret or not to:
        raise NotifyError("set SMTP_USER/SMTP_PASSWORD (or IMAP_USER/IMAP_PASSWORD)")

    msg = EmailMessage()
    msg["From"] = user
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    for path in attachments:
        if path.exists():
            msg.add_attachment(path.read_bytes(), maintype="text", subtype="plain",
                               filename=path.name)
    try:
        with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=60) as smtp:
            smtp.login(user, secret.get_secret_value())
            smtp.send_message(msg)
    except (smtplib.SMTPException, OSError) as exc:
        raise NotifyError(f"could not send the report email: {exc}") from exc
    return to
