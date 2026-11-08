from __future__ import annotations

import email
from email import policy
from pathlib import Path

import httpx
import respx

from job_hunter.capture import (
    CaptureItem,
    apply_capture,
    is_capture_email,
    parse_capture_email,
    stable_job_url,
)
from job_hunter.config import AppConfig, Employer, EmployersConfig
from job_hunter.models import JobPosting
from job_hunter.sources.employers import EmployerSource
from job_hunter.store import JobStore

ME = ["me@example.com"]


def _load(path: Path) -> email.message.Message:
    with path.open("rb") as fh:
        return email.message_from_binary_file(fh, policy=policy.default)


# ------------------------------------------------------------------ store

def test_store_roundtrip_and_merge(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.json")
    a = JobPosting(source="linkedin", channel="email", title="Risk Analyst", company="Bank",
                   location="Paris", url="https://www.linkedin.com/jobs/view/1")
    rec, new = store.upsert(a)
    assert new
    b = a.model_copy(update={"source": "indeed", "url": "https://fr.indeed.com/viewjob?jk=9",
                             "salary_text": "70k"})
    rec2, new2 = store.upsert(b)
    assert not new2 and rec2 is rec and rec.job.salary_text == "70k"
    store.save()
    again = JobStore(tmp_path / "jobs.json")
    assert again.find_by_url("https://fr.indeed.com/viewjob?jk=9") is not None


# ------------------------------------------------------------------ capture

def test_capture_email_detection(config: AppConfig, emails_dir: Path) -> None:
    msg = _load(emails_dir / "capture_jd.eml")
    cap = config.sources.capture
    assert is_capture_email(msg, cap, ME)
    assert not is_capture_email(msg, cap, ["someone@else.com"])  # sender not allowed
    assert not is_capture_email(msg, cap, [])                     # capture off without senders
    item = parse_capture_email(msg, cap)
    assert item is not None
    assert item.url and item.url.startswith("https://www.linkedin.com/comm/jobs/view/4486648624")
    assert item.title_hint == "Consultant risque de crédit"
    assert "simulation Monte Carlo" in item.text and "linkedin.com" not in item.text


def test_capture_matches_job_from_alert(config: AppConfig, tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.json")
    alert_job = JobPosting(source="linkedin", channel="email", title="CONSULTANTS RISQUE DE CREDIT",
                           company="Wise Associates", location="Paris",
                           url="https://www.linkedin.com/jobs/view/4486648624")
    rec, _ = store.upsert(alert_job)
    tracked = "https://www.linkedin.com/comm/jobs/view/4486648624/?trackingId=X&otpToken=Y"
    assert stable_job_url(tracked, config.sources.email) == alert_job.url
    got = apply_capture(store, CaptureItem(text="Full text " * 100, url=tracked), config.sources.email)
    assert got is rec and rec.description_source == "capture" and rec.has_full_text
    assert len(store.jobs) == 1


def test_capture_without_match_creates_job(config: AppConfig, tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.json")
    rec = apply_capture(store, CaptureItem(text="Quant role description " * 50,
                                           url="https://careers.example.com/job/42",
                                           title_hint="Quant Risk"), config.sources.email)
    assert rec.job.title == "Quant Risk" and rec.job.source == "manual"


# ------------------------------------------------------------------ employers

@respx.mock
def test_employer_apis(config: AppConfig) -> None:
    respx.get("https://boards-api.greenhouse.io/v1/boards/fundx/jobs?content=true").mock(
        return_value=httpx.Response(200, json={"jobs": [{
            "id": 11, "title": "Quant Risk Analyst", "location": {"name": "Paris"},
            "absolute_url": "https://boards.greenhouse.io/fundx/jobs/11",
            "content": "&lt;p&gt;Build &lt;b&gt;XVA&lt;/b&gt; models.&lt;/p&gt;",
            "updated_at": "2026-11-05T10:00:00-05:00"}]}))
    respx.get("https://api.lever.co/v0/postings/levco?mode=json").mock(
        return_value=httpx.Response(200, json=[{
            "id": "abc", "text": "Market Risk Modeller", "hostedUrl": "https://jobs.lever.co/levco/abc",
            "categories": {"location": "Remote", "commitment": "Full-time"},
            "descriptionPlain": "Model VaR.", "lists": [{"text": "Requirements",
                                                         "content": "<li>Python</li>"}],
            "workplaceType": "remote", "createdAt": 1793000000000}]))
    respx.get(url__startswith="https://api.ashbyhq.com/posting-api/job-board/down").mock(
        return_value=httpx.Response(404))
    cfg = EmployersConfig(enabled=True, companies=[
        Employer(name="Fund X", ats="greenhouse", board="fundx"),
        Employer(name="Lev Co", ats="lever", board="levco"),
        Employer(name="Down Inc", ats="ashby", board="down"),
    ])
    jobs = EmployerSource(cfg).fetch(config.search)
    assert [j.company for j in jobs] == ["Fund X", "Lev Co"]  # failing employer skipped
    gh, lv = jobs
    assert gh.description == "Build\nXVA\nmodels." and gh.location == "Paris"
    assert "Requirements\nPython" in (lv.description or "") and lv.remote_text == "remote"
    assert lv.published_at is not None
