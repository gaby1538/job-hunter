from __future__ import annotations

from email.message import EmailMessage
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from job_hunter import notify
from job_hunter.cli import main
from job_hunter.notify import NotifyError, send_report
from job_hunter.settings import Settings

from .conftest import ROOT

SENT: list[EmailMessage] = []


class FakeSMTP:
    def __init__(self, host: str, port: int, timeout: int) -> None:
        self.host, self.port = host, port

    def __enter__(self) -> FakeSMTP:
        return self

    def __exit__(self, *exc: Any) -> None:
        pass

    def login(self, user: str, password: str) -> None:
        assert password == "app-pass"

    def send_message(self, msg: EmailMessage) -> None:
        SENT.append(msg)


@pytest.fixture(autouse=True)
def fake_smtp(monkeypatch: pytest.MonkeyPatch) -> None:
    SENT.clear()
    monkeypatch.setattr(notify.smtplib, "SMTP_SSL", FakeSMTP)


def test_report_sent_to_yourself_by_default(tmp_path: Path) -> None:
    csv_file = tmp_path / "analysis.csv"
    csv_file.write_text("verdict,score\n")
    s = Settings(_env_file=None, imap_user="me@example.com", imap_password=SecretStr("app-pass"))
    to = send_report(s, "Subject", "Body", [csv_file, tmp_path / "missing.log"])
    assert to == "me@example.com"
    (msg,) = SENT
    assert msg["To"] == "me@example.com" and msg["Subject"] == "Subject"
    assert [a.get_filename() for a in msg.iter_attachments()] == ["analysis.csv"]


def test_missing_credentials() -> None:
    with pytest.raises(NotifyError):
        send_report(Settings(_env_file=None), "s", "b", [])


def test_cli_run_emails_shortlist(emails_dir: Path, tmp_path: Path,
                                  monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOB_HUNTER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("IMAP_USER", "me@example.com")
    monkeypatch.setenv("IMAP_PASSWORD", "app-pass")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    log = tmp_path / "run.log"
    code = main(["--config", str(ROOT / "config"), "run", "--sources", "email",
                 "--eml-dir", str(emails_dir), "--email-report", "--log-file", str(log)])
    assert code == 0
    (msg,) = SENT
    assert msg["Subject"].startswith("Job Hunter ")
    assert "Job shortlist" in msg.get_body(preferencelist=("plain",)).get_content()
    names = [a.get_filename() for a in msg.iter_attachments()]
    assert names == ["analysis.csv", "run.log"]
