# Job Hunter

Collects job ads every day from **authorized channels only**, gets the full
description wherever that is allowed, and has Claude analyse each job against your
profile: which requirements you cover, what is missing, onsite days, contract,
modelling vs coordination. A transparent score out of 100 gives a verdict:
**apply / consider / skip**. Every criterion and rule is a YAML parameter.

## Where the jobs come from

| Channel | Sites | Status |
|---|---|---|
| Email alerts | **Indeed, LinkedIn, HelloWork, Free-Work, Malt** | Rules built from real alerts (Nov 2026) |
| Email alerts | eFinancialCareers, APEC, Monster, Welcome to the Jungle | Rules ready but unverified: subscribe to their job alerts, then check with `parse-eml` |
| Adzuna API | Aggregates many French boards | Official API, free self-serve key |
| France Travail API | France Travail | Official API, **off by default** (new access requests currently paused) |
| RSS | Any board publishing a feed | Feed URLs in `sources.yaml` |

No site is scraped. Indeed retired its public API and forbids scraping; the
other boards have no public job API, so their **email alerts** are the route.
Alerts are read from your inbox in read-only mode.

## Getting the full job description

Job boards (Indeed, LinkedIn, HelloWork, ...) do not allow robots to fetch their
pages, so the tool never does. The full text comes from:

| Where | How | Effort |
|---|---|---|
| Malt emails, France Travail API | Already contain the full ad | Automatic |
| **Employer watchlist** | Employers' public hiring APIs (Greenhouse, Lever, Ashby), built for reuse. Add firms in `sources.yaml > employers` | Automatic |
| **Capture** | You open an ad you like, copy its text, email it to the alert inbox with a subject starting `JD` and the link on the first line. The next run attaches it to the right job and re-analyses it. Works from a phone. | One copy-paste per job |
| Alert excerpt | Everything else is analysed on the alert snippet; the report says so and asks for a capture if the job looks promising | Automatic |

Capture from the command line works too:
`python -m job_hunter capture --url <link> --file description.txt`.
Only senders listed in `CAPTURE_SENDERS` are accepted.

## How the analysis works

For each new job (or a job whose text changed), Claude receives your profile
(`config/profile.yaml`) and everything known about the job, and returns structured
JSON (validated by Pydantic): requirements with how well you cover each one
(strong / partial / missing), role match, modelling vs coordination, contract, onsite
days, years required, strengths to put forward, gaps to prepare, red flags.

**Nothing is taken on trust.** Every fact must come with a verbatim quote from the ad.
The code checks each quote against the text; a fact whose quote is not found is
discarded and listed in the report. If only an excerpt was available, most facts stay
unknown and the report asks you to capture the full ad.

### How the score works

The score is computed in Python from the verified facts, so the same facts always
give the same score. Default weights (edit them in `profile.yaml`, they must sum to 100):

| Criterion | Points | Full points when | Unknown |
|---|---|---|---|
| Role fit | 25 | Target role (adjacent role: half) | — |
| Requirements | 30 | You cover them (must-haves count double; partial = half) | 40% |
| Modelling depth | 20 | Hands-on modelling (mixed: 60%, coordination: 0) | 40% |
| Remote | 15 | Onsite days within your maximum (2/month) | 40% |
| Contract | 5 | Permanent or freelance | 50% |
| Seniority | 5 | Years asked ≤ yours + 1 | 50% |

A mostly-PMO/coordination role loses 25 points. Verdict: **apply** from 70 with at most
one missing must-have, **consider** from 50, otherwise **skip**. Internships are skipped.
More onsite days than your maximum caps the verdict at **consider**, noted
"would be apply if remote can be negotiated" (`onsite_limit_is_dealbreaker`).

### Cost

Model and cost cap are in `profile.yaml > analysis`. With `claude-sonnet-5-5` a job
costs a few cents (input $2, output $10 per million tokens); `max_jobs_per_run`
(default 40) caps each run, the rest waits for the next one. Your profile is sent as a
cached prompt prefix. Token counts are printed after each run.

## Output

Each run writes `data/runs/<Paris date-time>/`:
- `shortlist.md`: jobs to apply to / consider, score breakdown, requirement by
  requirement coverage, what to put forward, what to prepare, link to the ad
- `analysis.csv`: one line per analysed job (opens in Excel)
- `jobs.jsonl`, `jobs.csv`, `rejected.csv`, `summary.json`: this run's collection

All jobs and analyses are kept in `data/jobs.json` (to become PostgreSQL / Supabase).

## Project layout

```
config/
  search.yaml        # YOUR CRITERIA: queries, keywords, exclusions, contracts, max age
  sources.yaml       # sites on/off, email parsing rules per site, API settings
  profile.yaml       # YOUR PROFILE, scoring weights, Claude model and cost cap
src/job_hunter/
  settings.py        # secrets from environment variables (.env locally)
  config.py          # loads + validates the YAML (bad regex / typo = clear error)
  profile.py         # loads + validates profile.yaml (weights must sum to 100)
  models.py          # JobPosting: only explicit facts, unknown stays None
  store.py           # every job seen + best description + analysis (data/jobs.json)
  capture.py         # descriptions you copy and email with subject "JD"
  analysis/
    schema.py        # the JSON Claude must return
    analyzer.py      # Claude API call (structured outputs, cached profile)
    verify.py        # discards facts whose quote is not in the ad
    scoring.py       # deterministic score, verdict, confidence
    report.py        # shortlist.md + analysis.csv
  sources/
    base.py          # Source protocol + HTTP retry (network errors, 429, 5xx)
    email_parser.py  # generic alert-email parser driven by sources.yaml
    email_alerts.py  # IMAP inbox or folder of .eml files
    employers.py     # Greenhouse / Lever / Ashby public job APIs
    adzuna.py, france_travail.py, rss.py
  processing/
    normalize.py     # clean titles (H/F...), strip tracking params, explicit contract words
    filters.py       # applies search.yaml, keeps the reason for every rejection
    dedupe.py        # same title+company+city or same URL -> one job, other URLs kept
  pipeline.py        # runs sources (one failing source doesn't stop the run) + export
  cli.py             # command line
tests/               # 54 tests: anonymized real-layout alerts, mocked APIs, fake Claude
```

## How the email parser works

Each site has a rule in `sources.yaml`; no site-specific code.

1. **Which emails**: the sender address (`sender_patterns`) and optionally the subject.
   Only alert senders are listed, so recruiter messages and newsletters are ignored.
2. **Which jobs**: every link whose URL matches `job_url_patterns`. Click-tracking
   redirects (HelloWork) are decoded to the real job URL (`unwrap_redirects`).
3. **Which lines belong to a job**: in text alerts (Indeed, LinkedIn, Free-Work) the
   details sit above the job link; in HTML alerts (HelloWork) below the title.
4. **Fields**: `line_patterns` (regex with named groups) turn lines into company,
   location, salary, contract, remote policy or "posted 3 days ago". Lines no pattern
   recognises are kept as raw context, never guessed into a field.
5. **Single-offer emails** (Malt): title from a CSS selector, company from the subject,
   and labelled values (Lieu, Télétravail, Tarif journalier...).
6. **Stable URLs**: when a job id is found, the URL is rebuilt from it
   (`canonical_url`), so the same job always gets the same link.

What is deliberately *not* invented:
- Indeed estimates salaries that ads don't state; its salaries are flagged
  `salary_may_be_estimate`.
- A contract implied by the site (Malt = freelance) is marked `contract_source: site default`.
- "il y a 10 jours" becomes a publication date only relative to the email's own date.

**Privacy**: tracking links can embed your email address and personal tokens. They are
never stored; only the cleaned job URL is kept. Test fixtures are anonymized.

To check a rule on a real email, save it as `.eml` (Gmail: ⋮ > Download message) and run:

```bash
python -m job_hunter parse-eml path/to/alert.eml
```

To change the sample emails, edit and run `tests/fixtures/make_fixtures.py`.

## Run it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env            # then fill in ANTHROPIC_API_KEY and the rest

python -m job_hunter check-config
python -m job_hunter run --sources email --eml-dir tests/fixtures/emails   # demo on sample emails
python -m job_hunter run                                                   # all enabled sources
python -m job_hunter capture --url <link> --file jd.txt                    # add a description
python -m job_hunter shortlist                                             # rebuild the report
pytest
```

Without `ANTHROPIC_API_KEY`, jobs are still collected and stored; analysis runs once
the key is set.

## Setting up the inbox

1. Create a dedicated Gmail address for job alerts.
2. Subscribe to alerts on each site with that address (or forward existing alerts to it).
3. Turn on 2-step verification, create an App Password, put it in `IMAP_PASSWORD`.

## Next phases

PostgreSQL (Supabase) storage, daily HTML report by email, Streamlit dashboard
with application status, daily cloud schedule.
