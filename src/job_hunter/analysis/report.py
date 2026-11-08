"""Shortlist report: which jobs to apply to, why, and what to prepare."""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from job_hunter.store import JobStore, StoredJob

PARIS = ZoneInfo("Europe/Paris")
ORDER = {"apply": 0, "consider": 1, "skip": 2}
ICON = {"apply": "🟢 APPLY", "consider": "🟡 CONSIDER", "skip": "⚪ SKIP"}


def ranked(store: JobStore) -> list[StoredJob]:
    done = [r for r in store.jobs.values() if r.score]
    return sorted(done, key=lambda r: (ORDER[r.score["verdict"]], -r.score["total"]))  # type: ignore[index]


def _onsite(rec: StoredJob) -> str:
    a = rec.analysis or {}
    days = a.get("onsite_days_per_month")
    if days is not None:
        return f"{days:g} days/month onsite"
    return str(a.get("remote_policy", "unknown")).replace("_", " ")


def _job_md(rec: StoredJob) -> str:
    s, a, j = rec.score or {}, rec.analysis or {}, rec.job
    contract = a.get("contract", "unknown")
    if contract == "unknown":
        contract = j.contract_type.value
    head = f"### {j.title}" + (f" — {j.company}" if j.company else "")
    lines = [
        head,
        f"**{ICON[s['verdict']]} · {s['total']:g}/100** · confidence {s['confidence']}"
        f" · {_onsite(rec)} · contract {contract}"
        + (f" · {j.location}" if j.location else ""),
        "",
        a.get("summary", ""),
        "",
        "Score: " + ", ".join(
            f"{k.replace('_', ' ')} {s['parts'][k]:g}/{s['max_parts'][k]:g}" for k in s["parts"]
        ) + "".join(f", penalty {k.replace('_', ' ')} -{v:g}" for k, v in s["penalties"].items()),
    ]
    reqs = a.get("requirements", [])
    if reqs:
        mark = {"strong": "✅", "partial": "🟠", "missing": "❌"}
        lines.append("")
        lines.append("Requirements:")
        for r in reqs:
            tag = "must" if r["importance"] == "must_have" else "nice"
            lines.append(f"- {mark[r['coverage']]} ({tag}) {r['requirement']} — {r['coverage_reason']}")
    if a.get("strengths_to_highlight"):
        lines.append("\nPut forward: " + "; ".join(a["strengths_to_highlight"]))
    if a.get("gaps_to_address"):
        lines.append("Prepare: " + "; ".join(a["gaps_to_address"]))
    notes = [x for x in s.get("reasons", []) if "negotiat" in x or "onsite days" in x]
    if notes:
        lines.append("Note: " + "; ".join(notes))
    if a.get("red_flags"):
        lines.append("Watch out: " + "; ".join(a["red_flags"]))
    if s.get("needs_full_text"):
        lines.append("\n📋 **Only an excerpt was analysed.** Open the ad, copy its text and email it "
                     "with subject `JD` (link on the first line) to get a full analysis.")
    if a.get("discarded_unverified"):
        lines.append(f"\n_Discarded as unsupported by the text: {', '.join(a['discarded_unverified'])}_")
    if j.url:
        lines.append(f"\n[Open the ad]({j.url})")
    return "\n".join(lines)


def write_reports(store: JobStore, out_dir: Path, generated_at: datetime) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    jobs = ranked(store)
    counts = {v: sum(1 for r in jobs if r.score and r.score["verdict"] == v) for v in ORDER}
    md = [
        f"# Job shortlist — {generated_at.astimezone(PARIS):%d/%m/%Y %H:%M} (Paris)",
        "",
        f"{counts['apply']} to apply · {counts['consider']} to consider · {counts['skip']} skipped",
        "",
    ]
    for verdict in ("apply", "consider"):
        section = [r for r in jobs if r.score and r.score["verdict"] == verdict]
        if section:
            md.append(f"## {ICON[verdict]}\n")
            md.extend(_job_md(r) + "\n\n---\n" for r in section)
    skipped = [r for r in jobs if r.score and r.score["verdict"] == "skip"]
    if skipped:
        md.append(f"## {ICON['skip']}\n")
        for r in skipped:
            reasons = ", ".join(r.score["reasons"][:3]) if r.score else ""
            total = r.score["total"] if r.score else 0
            md.append(f"- {r.job.title} ({r.job.company or '?'}) — {total:g}/100: {reasons}")
    md_path = out_dir / "shortlist.md"
    md_path.write_text("\n".join(md) + "\n", encoding="utf-8")

    csv_path = out_dir / "analysis.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["verdict", "score", "confidence", "title", "company", "location",
                         "contract", "onsite", "role_match", "work_nature",
                         "missing_must_haves", "needs_full_text", "source", "url"])
        for r in jobs:
            s, a = r.score or {}, r.analysis or {}
            writer.writerow([
                s.get("verdict"), s.get("total"), s.get("confidence"), r.job.title,
                r.job.company or "", r.job.location or "", a.get("contract"), _onsite(r),
                a.get("role_match"), a.get("work_nature"),
                "; ".join(s.get("missing_must_haves", [])), s.get("needs_full_text"),
                r.job.source, r.job.url or "",
            ])
    return md_path, csv_path
