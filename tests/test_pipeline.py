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
    assert "Stage Risque de Marché" not in titles          # excluded keyword
    assert "Comptable fournisseurs" not in titles          # no include keyword
    assert result.errors == {"broken": "API down"}         # other sources still ran
    assert result.per_source["efinancialcareers"] == 3

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


def test_cli_extract_and_parse_eml(emails_dir: Path, tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    code = main(["--config", str(ROOT / "config"), "extract", "--sources", "email",
                 "--eml-dir", str(emails_dir), "--out", str(tmp_path)])
    assert code == 0
    assert "Kept" in capsys.readouterr().out

    main(["--config", str(ROOT / "config"), "parse-eml", str(emails_dir / "indeed_alert.eml")])
    out = capsys.readouterr().out
    assert "Site: indeed" in out and "Jobs found: 3" in out
