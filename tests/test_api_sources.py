import httpx
import pytest
import respx

from job_hunter.config import AppConfig
from job_hunter.models import ContractType
from job_hunter.settings import Settings
from job_hunter.sources.adzuna import AdzunaSource
from job_hunter.sources.base import SourceError
from job_hunter.sources.france_travail import FranceTravailSource
from job_hunter.sources.rss import parse_feed

ADZUNA_ITEM = {
    "id": "4242",
    "title": "Quantitative Risk Analyst",
    "company": {"display_name": "Banque Exemple"},
    "location": {"display_name": "Marseille, Bouches-du-Rhône"},
    "description": "Monte Carlo VaR and Expected Shortfall models.",
    "redirect_url": "https://www.adzuna.fr/details/4242?utm_medium=api",
    "created": "2026-11-07T10:00:00Z",
    "contract_type": "permanent",
    "salary_min": 70000,
    "salary_max": 85000,
    "salary_is_predicted": "0",
}


@respx.mock
def test_adzuna_maps_fields(config: AppConfig, settings: Settings) -> None:
    config.search.queries = ["market risk"]
    route = respx.get(url__regex=r"https://api\.adzuna\.com/v1/api/jobs/fr/search/1").mock(
        return_value=httpx.Response(200, json={"results": [ADZUNA_ITEM]})
    )
    jobs = AdzunaSource(config.sources.adzuna, settings).fetch(config.search)
    assert route.called
    assert route.calls[0].request.url.params["what_phrase"] == "market risk"
    job = jobs[0]
    assert job.company == "Banque Exemple"
    assert job.contract_type is ContractType.PERMANENT
    assert job.salary_min == 70000
    assert job.published_at is not None and job.published_at.tzinfo is not None


@respx.mock
def test_adzuna_predicted_salary_not_stored(config: AppConfig, settings: Settings) -> None:
    config.search.queries = ["xva"]
    item = {**ADZUNA_ITEM, "salary_is_predicted": "1", "contract_type": "contract"}
    respx.get(url__regex=r".*/search/1").mock(
        return_value=httpx.Response(200, json={"results": [item]})
    )
    job = AdzunaSource(config.sources.adzuna, settings).fetch(config.search)[0]
    assert job.salary_min is None and job.salary_max is None
    assert job.contract_type is ContractType.UNKNOWN  # "contract" is ambiguous


@respx.mock
def test_adzuna_retries_then_succeeds(config: AppConfig, settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("time.sleep", lambda _s: None)
    config.search.queries = ["xva"]
    respx.get(url__regex=r".*/search/1").mock(
        side_effect=[httpx.Response(503), httpx.Response(200, json={"results": [ADZUNA_ITEM]})]
    )
    assert len(AdzunaSource(config.sources.adzuna, settings).fetch(config.search)) == 1


def test_adzuna_requires_keys(config: AppConfig) -> None:
    with pytest.raises(SourceError, match="ADZUNA"):
        AdzunaSource(config.sources.adzuna, Settings(_env_file=None)).fetch(config.search)


@respx.mock
def test_france_travail(config: AppConfig, settings: Settings) -> None:
    assert config.sources.france_travail is not None
    config.search.queries = ["risque de marché"]
    respx.post(url__regex=r".*access_token.*").mock(
        return_value=httpx.Response(200, json={"access_token": "tok"})
    )
    search = respx.get(url__regex=r".*offres/search.*").mock(
        return_value=httpx.Response(206, json={"resultats": [{
            "id": "199XYZ",
            "intitule": "Analyste risque de liquidité (H/F)",
            "entreprise": {"nom": "Banque Exemple"},
            "lieuTravail": {"libelle": "13 - MARSEILLE 02"},
            "typeContrat": "CDI",
            "salaire": {"libelle": "Annuel de 60000 à 70000 Euros"},
            "dateCreation": "2026-11-06T09:00:00.000Z",
        }]})
    )
    jobs = FranceTravailSource(config.sources.france_travail, settings).fetch(config.search)
    assert search.calls[0].request.headers["Authorization"] == "Bearer tok"
    assert jobs[0].contract_type is ContractType.PERMANENT
    assert jobs[0].salary_text == "Annuel de 60000 à 70000 Euros"
    assert jobs[0].url and "199XYZ" in jobs[0].url


def test_rss_and_atom() -> None:
    rss = """<rss><channel>
      <item><title>Risk Modelling Specialist</title><link>https://board.example/1</link>
      <description>&lt;p&gt;XVA &amp;amp; CVA&lt;/p&gt;</description>
      <pubDate>Fri, 06 Nov 2026 08:00:00 +0100</pubDate></item>
    </channel></rss>"""
    jobs = parse_feed(rss, "board")
    assert jobs[0].title == "Risk Modelling Specialist"
    assert jobs[0].description == "XVA & CVA"
    assert jobs[0].published_at and jobs[0].published_at.utcoffset().total_seconds() == 0

    atom = """<feed xmlns="http://www.w3.org/2005/Atom"><entry>
      <title>Market Risk Analyst</title><link href="https://board.example/2"/>
      <id>2</id><updated>2026-11-06T08:00:00Z</updated></entry></feed>"""
    assert parse_feed(atom, "board")[0].url == "https://board.example/2"
