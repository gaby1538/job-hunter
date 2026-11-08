import csv
import json
from datetime import UTC, datetime
from pathlib import Path

from job_hunter.cli import main
from job_hunter.config import AppConfig, SearchCriteria
from job_hunter.models import JobPosting
from job_hunter.pipeline import build_sources, export, run
from job_hunter.settings import Settings

from .conftest import ROOT

NOW = datetime(2026, 11, 8, 18, 30, tzinfo=UTC)


class FailingSource:
    name = "broken"

    def fetch(self, criteria: SearchCriteria) -> list[JobPosting]:
        raise RuntimeError("API down")


def test_end_to_end_from_eml(config: AppConfig, settings: Settings, emails_dir: Path,
                             tmp_path: Path) -> None:
    sources = build_sources(config, settings, only={"email"}, eml_dir=emails_dir)
    result = run(config, [*sources, FailingSource()], now=NOW)

    titles = sorted(j.title for j in result.kept)
    assert "Market Risk Quant Analyst" in titles           # gender tag cleaned
    assert "Analyste quantitatif - Risques de crédit" in titles
    assert "Quant Risk Analyst - XVA" in titles
    assert "Consultant Risque de Marché" in titles         # Malt
    assert "Stage Risque de Marché" not in titles          # excluded keyword
    reasons = {r.job.title: r.reason for r in result.rejected}
    assert reasons["Chef de projet Power BI"] == "no include keyword found"
    assert "excluded keyword 'stage'" in reasons["Stage - 6 mois - Analyste Investissement"]
    assert result.errors == {"broken": "API down"}         # other sources still ran
    assert result.per_source == {
        "efinancialcareers": 3, "free_work": 2, "hellowork": 4,
        "indeed": 2, "linkedin": 2, "malt": 1,
    }

    run_dir = export(result, tmp_path)
    assert run_dir.name == "2026-11-08_193000"             # folder named in Paris time
    lines = (run_dir / "jobs.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(result.kept)
    record = json.loads(lines[0])
    assert record["collected_at"].endswith("Z") or "+00:00" in record["collected_at"]
    with (run_dir / "rejected.csv").open(encoding="utf-8-sig") as fh:
        assert any(row["reason"] for row in csv.DictReader(fh))
    summary = json.loads((run_dir / "summary.json").read_text())
    assert summary["source_errors"] == {"broken": "API down"}


def test_daily_run_with_capture_and_analysis(config: AppConfig, settings: Settings,
                                             emails_dir: Path, tmp_path: Path) -> None:
    from job_hunter.analysis.analyzer import Analyzer
    from job_hunter.analysis.schema import Requirement
    from job_hunter.pipeline import daily
    from job_hunter.profile import load_profile
    from job_hunter.store import JobStore

    from .test_analysis import FakeMessages, make_analysis

    profile = load_profile(ROOT / "config")
    store = JobStore(tmp_path / "jobs.json")
    sources = build_sources(config, settings, only={"email"}, eml_dir=emails_dir)
    # Quotes taken from the captured ad: they verify on that job, not on the others.
    fake = FakeMessages(make_analysis(
        work_nature_quote="Implémenter les ajustements CVA et FVA en Python",
        remote_policy="hybrid", onsite_days_per_month=13.0,
        remote_quote="Télétravail : 2 jours par semaine",
        years_quote="Au moins 5 ans d'expérience",
        requirements=[
            Requirement(requirement="Python and SQL", importance="must_have",
                        quote="Maîtrise de Python et SQL indispensable", coverage="strong",
                        coverage_reason="Python expert, SQL strong"),
            Requirement(requirement="SA-CCR", importance="nice_to_have",
                        quote="Connaissance de la réglementation SA-CCR appréciée",
                        coverage="partial", coverage_reason="Counterparty risk strong"),
        ],
    ))
    res = daily(config, sources, store, Analyzer(profile, fake), tmp_path / "runs",
                analysis_limit=50, now=NOW)

    assert res.captures_applied == 1
    # The captured text was attached to the LinkedIn job from the alert, not a new job.
    rec = store.find_by_url("https://www.linkedin.com/jobs/view/4486648624")
    assert rec is not None and rec.description_source == "capture" and rec.has_full_text
    assert rec.analysis is not None and rec.analysis["text_source"] == "capture"
    assert res.analysis.analysed == len(store.jobs)
    # The captured job was analysed first.
    assert "simulation Monte Carlo" in fake.calls[0]["messages"][0]["content"]

    assert rec.score is not None
    # 80/100 but 13 onsite days/month > your max of 2: capped at "consider".
    assert rec.score["total"] == 80.0 and rec.score["parts"]["remote"] == 0.0
    assert rec.score["verdict"] == "consider"
    assert "would be 'apply' if remote can be negotiated" in rec.score["reasons"]
    # On the other jobs the same quotes are not found: those facts are discarded.
    other = store.find_by_url(
        "https://www.free-work.com/fr/tech-it/job-mission/analyste-risque/quant-risk-analyst-xva")
    assert other is not None and other.analysis is not None
    assert other.analysis["discarded_unverified"] and other.score["verdict"] != "apply"
    assert other.score["needs_full_text"]

    shortlist = res.shortlist.read_text(encoding="utf-8")
    assert "Job shortlist" in shortlist and "0 to apply" in shortlist
    assert "remote can be negotiated" in shortlist
    assert "Only an excerpt was analysed" in shortlist
    assert (tmp_path / "jobs.json").exists()

    # Second run: nothing new to analyse.
    again = daily(config, build_sources(config, settings, only={"email"}, eml_dir=emails_dir),
                  JobStore(tmp_path / "jobs.json"), Analyzer(profile, fake),
                  tmp_path / "runs", analysis_limit=50, now=NOW)
    assert again.new_jobs == 0 and again.analysis.analysed == 0


def test_cli_extract_capture_and_parse_eml(emails_dir: Path, tmp_path: Path, capsys,  # type: ignore[no-untyped-def]
                                           monkeypatch) -> None:
    monkeypatch.setenv("JOB_HUNTER_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cfg = ["--config", str(ROOT / "config")]
    assert main([*cfg, "extract", "--sources", "email", "--eml-dir", str(emails_dir)]) == 0
    out = capsys.readouterr().out
    assert "Collected" in out and "Shortlist" in out

    desc = tmp_path / "jd.txt"
    desc.write_text("Quantitative risk modelling role. " * 20, encoding="utf-8")
    assert main([*cfg, "capture", "--url", "https://www.linkedin.com/comm/jobs/view/4486648624/?trk=x",
                 "--file", str(desc)]) == 0
    assert "CONSULTANTS RISQUE DE CREDIT" in capsys.readouterr().out  # matched the alert job

    main([*cfg, "parse-eml", str(emails_dir / "indeed_alert.eml")])
    out = capsys.readouterr().out
    assert "Site: indeed" in out and "Jobs found: 2" in out and "may be the site's own estimate" in out
