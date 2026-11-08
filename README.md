# Job Hunter — extraction prototype

Collects job ads every day from **authorized channels only**, normalizes them,
filters them with your criteria, merges duplicates and exports the result.
Every criterion and every site rule is a YAML parameter: no code change needed.

## Where the jobs come from

| Channel | Sites | How |
|---|---|---|
| Email alerts | eFinancialCareers, Indeed, Monster, Malt, HelloWork, APEC, LinkedIn, Free-Work, Welcome to the Jungle (+ any site you add) | Alerts you subscribe to, read from a dedicated inbox via IMAP (read-only) |
| Adzuna API | Aggregates many French boards | Official API, free self-serve key |
| France Travail API | France Travail | Official API, **off by default** (new access requests currently paused) |
| RSS | Any board publishing a feed | Feed URLs in `sources.yaml` |

No site is scraped. Indeed retired its public API and forbids scraping; the
other boards have no public job API, so their **email alerts** are the route.

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
tests/               # 24 tests, synthetic sample emails + mocked API responses
```

## How the email parser works

1. The sender (`From`) picks the site, using `sender_patterns`.
2. Every link whose URL matches the site's `job_url_patterns` is a job; its link text is the title.
3. Links like "Unsubscribe" or "See all" are ignored (`ignore_link_texts`).
4. The text lines next to the link (company, city, salary...) are stored as `context`.
   They become fields only if you set `context_fields`, e.g. `[company, location]`,
   once you've checked a real email. Until then nothing is guessed.

The URL patterns in `sources.yaml` are first guesses. To check one site, save one of its
alert emails as `.eml` (Gmail: ⋮ > Download message) and run:

```bash
python -m job_hunter parse-eml path/to/alert.eml
```

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
