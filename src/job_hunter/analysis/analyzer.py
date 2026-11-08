"""Analyse jobs against your profile with the Claude API (structured outputs)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from job_hunter.analysis.schema import JobAnalysis
from job_hunter.analysis.scoring import score
from job_hunter.analysis.verify import verify
from job_hunter.models import utc_now
from job_hunter.profile import Profile
from job_hunter.store import JobStore, StoredJob

logger = logging.getLogger(__name__)

PROMPT_VERSION = "2026-11-08.1"

SYSTEM_PROMPT = """You analyse job advertisements for one candidate, whose profile follows.
Your output is checked by code, so follow these rules exactly.

Facts
- Use ONLY the job text inside <job>. Never use outside knowledge about the employer.
- If the text does not state something, answer unknown / null. Do not guess.
- Every quote must be copied character for character from the job text (any language),
  short (a few words to one sentence). A fact whose quote is not found is discarded.
- The text may be only an excerpt from an alert email. Then most facts are unknown:
  say so rather than filling gaps.

Judgements
- requirements: list what the job asks for (skills, experience, tools, domains,
  qualifications, languages). For each, judge coverage by the candidate profile:
  strong = clearly in the profile; partial = related or lower level; missing = absent.
- role_match: target if it matches a target role, adjacent if it matches an adjacent
  role, off_target otherwise. Judge the actual work, not just the title.
- work_nature: modelling_heavy if the core is building, implementing or validating
  quantitative models; coordination_heavy if mostly project management, PMO, reporting
  coordination or business analysis; mixed otherwise.
- onsite_days_per_month: only when the text states onsite or remote days.
  Example: "2 jours de télétravail par semaine" on a 5-day week = 3 onsite days/week
  = about 13 per month. "Full remote" = 0.
- Write role_match_reason, coverage_reason, red_flags, strengths_to_highlight,
  gaps_to_address and summary in English, briefly.
"""


class MessagesClient(Protocol):
    @property
    def parse(self) -> Callable[..., Any]: ...


class AnalysisError(Exception):
    pass


@dataclass
class AnalysisRun:
    analysed: int = 0
    failed: int = 0
    skipped_limit: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    errors: list[str] = field(default_factory=list)


class Analyzer:
    def __init__(self, profile: Profile, messages: MessagesClient) -> None:
        self.profile = profile
        self.messages = messages

    @classmethod
    def from_api_key(cls, profile: Profile, api_key: str) -> Analyzer:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key, max_retries=4, timeout=120)
        return cls(profile, client.messages)

    def _call(self, text: str) -> tuple[JobAnalysis, dict[str, int]]:
        response = self.messages.parse(
            model=self.profile.analysis.model,
            max_tokens=self.profile.analysis.max_tokens,
            system=[
                {"type": "text", "text": SYSTEM_PROMPT},
                {
                    "type": "text",
                    "text": "Candidate profile:\n" + self.profile.as_prompt(),
                    "cache_control": {"type": "ephemeral"},
                },
            ],
            messages=[{"role": "user", "content": f"<job>\n{text}\n</job>"}],
            output_format=JobAnalysis,
        )
        if response.stop_reason in ("max_tokens", "refusal"):
            raise AnalysisError(f"analysis stopped: {response.stop_reason}")
        parsed = response.parsed_output
        if not isinstance(parsed, JobAnalysis):
            raise AnalysisError("no structured output returned")
        usage = getattr(response, "usage", None)
        tokens = {
            "input": int(getattr(usage, "input_tokens", 0) or 0),
            "output": int(getattr(usage, "output_tokens", 0) or 0),
        }
        return parsed, tokens

    def analyse(self, rec: StoredJob) -> dict[str, int]:
        text = rec.analysis_text()
        raw, tokens = self._call(text)
        checked, verification = verify(raw, text)
        result = score(
            checked, self.profile,
            has_full_text=rec.has_full_text, source_contract=rec.job.contract_type,
        )
        rec.analysis = {
            **checked.model_dump(mode="json"),
            "discarded_unverified": verification.discarded,
            "model": self.profile.analysis.model,
            "prompt_version": PROMPT_VERSION,
            "text_source": rec.description_source,
        }
        rec.score = result.model_dump(mode="json")
        rec.analyzed_text_hash = rec.text_hash()
        rec.analyzed_at = utc_now()
        return tokens

    def analyse_pending(self, store: JobStore, limit: int) -> AnalysisRun:
        """Analyse jobs that are new or whose text changed (e.g. a captured description)."""
        run = AnalysisRun()
        pending = [r for r in store.jobs.values() if r.needs_analysis]
        # Captured descriptions first: you asked for those explicitly.
        pending.sort(key=lambda r: (r.description_source != "capture", r.first_seen_at))
        for rec in pending:
            if run.analysed >= limit:
                run.skipped_limit += 1
                continue
            try:
                tokens = self.analyse(rec)
            except Exception as exc:  # noqa: BLE001 - one failure must not stop the batch
                run.failed += 1
                run.errors.append(f"{rec.job.title}: {exc}")
                logger.error("analysis failed for %s: %s", rec.job.title, exc)
                continue
            run.analysed += 1
            run.input_tokens += tokens["input"]
            run.output_tokens += tokens["output"]
        return run
