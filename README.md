# Job Hunter — extraction prototype

Collects job ads every day from **authorized channels only**, normalizes them,
filters them with your criteria, merges duplicates and exports the result.
Every criterion and every site rule is a YAML parameter: no code change needed.

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

## Project layout

```
config/
  search.yaml        # YOUR CRITERIA: queries, keywords, exclusions, contracts, max age
  sources.yaml       # sites on/off, email parsing rules per site, API settings
src/job_hunter/
  settings.py        # secrets from environment variables (.env locally)
  config.py          # loads + validates the YAML (bad regex / typo = clear error)
  models.py          # JobPosting: only explicit facts, unknown stays None
  sources/
    base.py          # Source protocol + HTTP retry (network errors, 429, 5xx)
    email_parser.py  # generic alert-email parser driven by sources.yaml
    email_alerts.py  # IMAP inbox or folder of .eml files
    adzuna.py, france_travail.py, rss.py
  processing/
    normalize.py     # clean titles (H/F...), strip tracking params, explicit contract words
    filters.py       # applies search.yaml, keeps the reason for every rejection
    dedupe.py        # same title+company+city or same URL -> one job, other URLs kept
  pipeline.py        # runs sources (one failing source doesn't stop the run) + export
  cli.py             # command line
tests/               # 36 tests: anonymized real-layout alerts + mocked API responses
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
cp .env.example .env            # then fill in the credentials you have

python -m job_hunter check-config
python -m job_hunter extract --sources email --eml-dir tests/fixtures/emails   # demo, no credentials
python -m job_hunter extract                                                   # all enabled sources
pytest
```

Each run writes `data/runs/<Paris date-time>/`:
- `jobs.jsonl`: full data, timestamps in UTC, raw payload kept for audit
- `jobs.csv`: readable, dates in Europe/Paris time (opens in Excel)
- `rejected.csv`: every filtered-out job with the reason
- `summary.json`: counts per site, duplicates merged, source errors

## Setting up the inbox

1. Create a dedicated Gmail address for job alerts.
2. Subscribe to alerts on each site with that address (or forward existing alerts to it).
3. Turn on 2-step verification, create an App Password, put it in `IMAP_PASSWORD`.

## Next phases

PostgreSQL (Supabase) storage, Claude analysis and scoring, daily HTML report,
Streamlit dashboard, daily cloud schedule.
