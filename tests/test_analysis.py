"""Analysis tests. Claude is replaced by a fake client: no network, no cost."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from job_hunter.analysis.analyzer import Analyzer
from job_hunter.analysis.schema import JobAnalysis, Requirement
from job_hunter.analysis.scoring import score
from job_hunter.analysis.verify import quote_in_text, verify
from job_hunter.config import ConfigError
from job_hunter.models import ContractType, JobPosting
from job_hunter.profile import Profile, load_profile
from job_hunter.store import JobStore

from .conftest import ROOT

TEXT = """Title: Quantitative Risk Analyst
Job description:
Vous développerez les modèles de VaR et d'Expected Shortfall par simulation Monte Carlo.
Profil : 5 ans d'expérience minimum. Python et SQL indispensables.
Connaissance de FRTB appréciée. CDI. Télétravail complet possible."""


def make_analysis(**over: Any) -> JobAnalysis:
    base: dict[str, Any] = dict(
        role_match="target", role_match_reason="Quant risk role", closest_target_role=None,
        work_nature="modelling_heavy", work_nature_quote="Vous développerez les modèles de VaR",
        contract="permanent", contract_quote="CDI",
        remote_policy="full_remote", onsite_days_per_month=0.0,
        remote_quote="Télétravail complet possible",
        required_years_min=5, years_quote="5 ans d'expérience minimum",
        location=None, risk_domains=["market"],
        requirements=[
            Requirement(requirement="Python", importance="must_have",
                        quote="Python et SQL indispensables", coverage="strong",
                        coverage_reason="Python expert"),
            Requirement(requirement="FRTB", importance="nice_to_have",
                        quote="Connaissance de FRTB appréciée", coverage="partial",
                        coverage_reason="VaR/ES expert, FRTB not listed"),
        ],
        languages_required=[], red_flags=[], strengths_to_highlight=["VaR and ES"],
        gaps_to_address=["FRTB"], summary="Market risk modelling role. Strong fit.",
    )
    base.update(over)
    return JobAnalysis(**base)


@pytest.fixture()
def profile() -> Profile:
    return load_profile(ROOT / "config")


# ------------------------------------------------------------------ verification

def test_quote_matching_is_tolerant_to_spacing_case_and_apostrophes() -> None:
    assert quote_in_text("5 ans d’expérience   MINIMUM", TEXT)
    assert quote_in_text("Vous développerez ... Monte Carlo", TEXT)
    assert not quote_in_text("10 ans d'expérience", TEXT)
    assert not quote_in_text("", TEXT)


def test_unsupported_facts_are_discarded() -> None:
    invented = make_analysis(
        contract="freelance", contract_quote="Mission freelance",   # not in the text
        required_years_min=8, years_quote="8 ans",                  # not in the text
        requirements=[Requirement(requirement="C++", importance="must_have",
                                  quote="C++ obligatoire", coverage="missing",
                                  coverage_reason="not in profile")],
    )
    checked, v = verify(invented, TEXT)
    assert checked.contract == "unknown" and checked.required_years_min is None
    assert checked.requirements == []
    assert len(v.discarded) == 3
    assert checked.remote_policy == "full_remote"  # supported fact kept


# ------------------------------------------------------------------ scoring

def test_strong_fit_is_apply(profile: Profile) -> None:
    s = score(make_analysis(), profile, has_full_text=True)
    assert s.verdict == "apply"
    # role 25 + requirements (2*1 + 1*0.5)/3*30 = 25 + modelling 20 + remote 15 + contract 5 + seniority 5
    assert s.total == 95.0
    assert s.parts["requirements"] == 25.0
    assert s.confidence == "high" and not s.needs_full_text


def test_coordination_role_is_penalised(profile: Profile) -> None:
    s = score(make_analysis(work_nature="coordination_heavy", role_match="adjacent"),
              profile, has_full_text=True)
    assert s.penalties == {"coordination_heavy": 25}
    assert s.verdict == "skip"


def test_too_many_onsite_days_scores_zero_remote(profile: Profile) -> None:
    s = score(make_analysis(remote_policy="hybrid", onsite_days_per_month=13.0),
              profile, has_full_text=True)
    assert s.parts["remote"] == 0.0
    assert "too many onsite days" in s.reasons
    assert s.total == 80.0 and s.verdict == "consider"  # dealbreaker caps "apply"
    assert "would be 'apply' if remote can be negotiated" in s.reasons


def test_missing_must_haves_block_apply(profile: Profile) -> None:
    reqs = [Requirement(requirement=f"skill {i}", importance="must_have", quote="CDI",
                        coverage="missing", coverage_reason="absent") for i in range(2)]
    reqs.append(Requirement(requirement="Python", importance="must_have", quote="CDI",
                            coverage="strong", coverage_reason="ok"))
    s = score(make_analysis(requirements=reqs * 3), profile, has_full_text=True)
    assert len(s.missing_must_haves) == 6
    assert s.verdict != "apply"


def test_excerpt_only_asks_for_full_text(profile: Profile) -> None:
    unknowns = make_analysis(
        work_nature="unknown", work_nature_quote=None, contract="unknown", contract_quote=None,
        remote_policy="unknown", onsite_days_per_month=None, remote_quote=None,
        required_years_min=None, years_quote=None, requirements=[],
    )
    s = score(unknowns, profile, has_full_text=False)
    assert s.verdict == "consider" and s.needs_full_text and s.confidence == "low"


def test_source_contract_used_when_text_silent(profile: Profile) -> None:
    a = make_analysis(contract="unknown", contract_quote=None)
    assert score(a, profile, has_full_text=True,
                 source_contract=ContractType.FREELANCE).parts["contract"] == 5.0
    assert score(make_analysis(contract="internship"), profile,
                 has_full_text=True).verdict == "skip"


def test_weights_must_sum_to_100(tmp_path: Any) -> None:
    text = (ROOT / "config" / "profile.yaml").read_text().replace("role_fit: 25", "role_fit: 30")
    (tmp_path / "profile.yaml").write_text(text)
    with pytest.raises(ConfigError, match="sum to 100"):
        load_profile(tmp_path)


# ------------------------------------------------------------------ analyzer

class FakeMessages:
    def __init__(self, analysis: JobAnalysis | None = None, fail_on: str | None = None) -> None:
        self.analysis = analysis or make_analysis()
        self.fail_on = fail_on
        self.calls: list[dict[str, Any]] = []

    def parse(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.fail_on and self.fail_on in kwargs["messages"][0]["content"]:
            raise RuntimeError("API error")
        return SimpleNamespace(parsed_output=self.analysis, stop_reason="end_turn",
                               usage=SimpleNamespace(input_tokens=1000, output_tokens=500))


def _store_with(tmp_path: Any, *titles: str) -> JobStore:
    store = JobStore(tmp_path / "jobs.json")
    for t in titles:
        store.upsert(JobPosting(source="test", channel="api", title=t, company="Bank",
                                description=TEXT))
    return store


def test_analyse_pending_respects_limit_and_isolates_failures(tmp_path: Any,
                                                              profile: Profile) -> None:
    store = _store_with(tmp_path, "Risk A", "Risk B", "Risk C")
    fake = FakeMessages(fail_on="Risk B")
    run = Analyzer(profile, fake).analyse_pending(store, limit=10)
    assert (run.analysed, run.failed) == (2, 1)
    assert run.input_tokens == 2000

    call = fake.calls[0]
    assert call["output_format"] is JobAnalysis
    assert call["model"] == profile.analysis.model
    assert call["system"][1]["cache_control"] == {"type": "ephemeral"}  # profile cached
    assert "Candidate profile" in call["system"][1]["text"]

    fake.fail_on = None
    run = Analyzer(profile, fake).analyse_pending(store, limit=10)
    assert run.analysed == 1  # only the failed one is retried

    limited = _store_with(tmp_path / "x", "R1", "R2", "R3")
    run = Analyzer(profile, FakeMessages()).analyse_pending(limited, limit=1)
    assert (run.analysed, run.skipped_limit) == (1, 2)


def test_new_description_triggers_reanalysis(tmp_path: Any, profile: Profile) -> None:
    store = _store_with(tmp_path, "Risk A")
    analyzer = Analyzer(profile, FakeMessages())
    analyzer.analyse_pending(store, limit=10)
    rec = next(iter(store.jobs.values()))
    assert not rec.needs_analysis
    assert rec.analysis and rec.analysis["prompt_version"]
    store.attach_description(rec, TEXT + "\nNouveau paragraphe.")
    assert rec.needs_analysis and rec.description_source == "capture"


def test_stop_reason_max_tokens_is_an_error(tmp_path: Any, profile: Profile) -> None:
    store = _store_with(tmp_path, "Risk A")
    fake = FakeMessages()
    fake.parse = lambda **_: SimpleNamespace(  # type: ignore[method-assign]
        parsed_output=None, stop_reason="max_tokens", usage=None)
    run = Analyzer(profile, fake).analyse_pending(store, limit=5)
    assert run.failed == 1 and "max_tokens" in run.errors[0]
