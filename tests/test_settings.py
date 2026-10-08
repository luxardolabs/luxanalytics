"""Production refuses development-only settings at startup (LUXANALYTI-22)."""

import pytest
from pydantic import ValidationError

from app.core.config import Settings

REQUIRED = {
    "DATABASE_URL": "postgresql+asyncpg://u:p@h/d",
    "DATABASE_URL_SYNC": "postgresql://u:p@h/d",
    "SECRET_KEY": "s",
}
SAFE = {
    "ENVIRONMENT": "production",
    "DEBUG": False,
    "EXTERNAL_URL": "https://analytics.example.com",
    "DASHBOARD_USERNAME": "admin",
    "DASHBOARD_PASSWORD": "a-long-random-production-password",
}


def _settings(**overrides: object) -> Settings:
    values = {**REQUIRED, **SAFE, **overrides}
    return Settings.model_validate(values)


def test_a_safe_production_configuration_starts() -> None:
    assert _settings().ENVIRONMENT == "production"


@pytest.mark.parametrize("password", ["admin", "changeme", "ChangeMe", "password"])
def test_a_guessable_password_is_refused_in_production(password: str) -> None:
    with pytest.raises(ValidationError, match="guessable"):
        _settings(DASHBOARD_PASSWORD=password)


def test_the_password_may_not_equal_the_username() -> None:
    with pytest.raises(ValidationError, match="guessable"):
        _settings(DASHBOARD_USERNAME="ops", DASHBOARD_PASSWORD="ops")


def test_debug_is_refused_in_production() -> None:
    with pytest.raises(ValidationError, match="DEBUG"):
        _settings(DEBUG=True)


def test_external_url_is_required_in_production() -> None:
    with pytest.raises(ValidationError, match="EXTERNAL_URL"):
        _settings(EXTERNAL_URL=None)


def test_development_is_not_held_to_it() -> None:
    dev = _settings(
        ENVIRONMENT="dev", DEBUG=True, DASHBOARD_PASSWORD="admin", EXTERNAL_URL=None
    )
    assert dev.DEBUG


@pytest.mark.parametrize(
    "environment", ["Production", "PROD", "production ", "prd", "", "staging"]
)
def test_anything_not_named_development_is_held_to_production(environment: str) -> None:
    """Fail closed: a typo or a new name must never switch the checks off (adversarial pass 3)."""
    with pytest.raises(ValidationError, match="DEBUG"):
        _settings(ENVIRONMENT=environment, DEBUG=True)


@pytest.mark.parametrize("environment", ["dev", "Development", " test "])
def test_the_development_names_are_matched_loosely(environment: str) -> None:
    assert not _settings(ENVIRONMENT=environment, DEBUG=True).is_production


@pytest.mark.parametrize("password", [" ", "1", " admin", "admin123", " Admin "])
def test_a_short_or_padded_default_password_is_refused(password: str) -> None:
    with pytest.raises(ValidationError, match="guessable"):
        _settings(DASHBOARD_PASSWORD=password)


@pytest.mark.parametrize(
    "url", ["", " ", "analytics.example.com", "ftp://a.b", "https://"]
)
def test_external_url_must_be_an_http_url_with_a_host(url: str) -> None:
    with pytest.raises(ValidationError, match="EXTERNAL_URL"):
        _settings(EXTERNAL_URL=url)
