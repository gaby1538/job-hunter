from pathlib import Path

import pytest

from job_hunter.config import ConfigError, load_config
from job_hunter.models import ContractType

from .conftest import ROOT


def test_shipped_config_is_valid() -> None:
    cfg = load_config(ROOT / "config")
    assert cfg.search.queries
    assert "indeed" in cfg.sources.email.sites
    assert ContractType.UNKNOWN in cfg.search.contract_types


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="missing"):
        load_config(tmp_path)


def test_invalid_regex_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "search.yaml").write_text("queries: [risk]\n")
    (tmp_path / "sources.yaml").write_text(
        "email:\n  sites:\n    bad:\n      sender_patterns: [x]\n      job_url_patterns: ['([']\n"
    )
    with pytest.raises(ConfigError, match="invalid regex"):
        load_config(tmp_path)


def test_unknown_contract_type_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "search.yaml").write_text("queries: [risk]\ncontract_types: [permanent, gig]\n")
    (tmp_path / "sources.yaml").write_text("{}\n")
    with pytest.raises(ConfigError):
        load_config(tmp_path)
