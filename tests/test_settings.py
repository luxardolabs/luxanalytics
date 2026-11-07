"""Production refuses development-only settings at startup (LUXANALYTI-22)."""

from pathlib import Path

import pytest
import redis.asyncio as redis
from limits.storage import storage_from_string
from pydantic import ValidationError

from app.core.config import Settings

REQUIRED = {
    "DATABASE_URL": "postgresql+asyncpg://u:p@h/d",
    "SECRET_KEY": "s" * 32,
    "ALLOWED_HOSTS": "analytics.example.com",
}
SAFE = {
    "ENVIRONMENT": "production",
    "DEBUG": False,
    "EXTERNAL_URL": "https://analytics.example.com",
    "DASHBOARD_USERNAME": "admin",
    "DASHBOARD_PASSWORD": "a-long-random-production-password",
    "DASHBOARD_SESSION_SECRET": "k" * 40,
}


def _settings(**overrides: object) -> Settings:
    values = {**REQUIRED, **SAFE, **overrides}
    return Settings.model_validate(values)


def test_a_safe_production_configuration_starts() -> None:
    assert _settings().ENVIRONMENT == "production"


@pytest.mark.parametrize("secret_key", ["", "short-secret-value", "s" * 31])
def test_an_empty_or_short_secret_key_is_refused(secret_key: str) -> None:
    with pytest.raises(ValidationError, match="SECRET_KEY"):
        _settings(SECRET_KEY=secret_key)


def test_a_refused_setting_does_not_print_its_value() -> None:
    with pytest.raises(ValidationError) as refused:
        _settings(SECRET_KEY="short-secret-value")

    assert "short-secret-value" not in str(refused.value)


@pytest.mark.parametrize(
    "secret", ["x", "short-secret-value", "s" * 31, " " * 32, "\t" * 32]
)
def test_a_short_or_blank_session_signing_key_is_refused(secret: str) -> None:
    with pytest.raises(ValidationError, match="DASHBOARD_SESSION_SECRET") as refused:
        _settings(DASHBOARD_SESSION_SECRET=secret)

    assert "short-secret-value" not in str(refused.value)


@pytest.mark.parametrize("secret", [None, ""])
def test_production_refuses_an_unset_or_empty_session_signing_key(
    secret: str | None,
) -> None:
    with pytest.raises(ValidationError, match="DASHBOARD_SESSION_SECRET"):
        _settings(DASHBOARD_SESSION_SECRET=secret)


@pytest.mark.parametrize("secret", [None, ""])
def test_development_starts_without_a_session_signing_key(secret: str | None) -> None:
    loaded = _settings(ENVIRONMENT="dev", DASHBOARD_SESSION_SECRET=secret)

    assert loaded.DASHBOARD_SESSION_SECRET is None


def test_a_long_session_signing_key_starts() -> None:
    assert _settings(DASHBOARD_SESSION_SECRET="s" * 32).DASHBOARD_SESSION_SECRET == (
        "s" * 32
    )


@pytest.mark.parametrize(
    "value",
    [
        '{"app": "hmac-secret-value"',
        '["hmac-secret-value"]',
        '{"app": ["hmac-secret-value"]}',
    ],
)
def test_malformed_hmac_keys_refuse_startup_without_printing_them(value: str) -> None:
    with pytest.raises(ValidationError, match="HMAC_KEYS") as refused:
        _settings(HMAC_KEYS=value)

    assert "hmac-secret-value" not in str(refused.value)


@pytest.mark.parametrize(
    "value",
    [
        '{"app": "   "}',
        '{"app": ""}',
        '{"app": "\\t\\n"}',
        '{"a": "real-secret", "b": " "}',
    ],
)
def test_a_blank_hmac_secret_is_refused(value: str) -> None:
    with pytest.raises(ValidationError, match="HMAC_KEYS") as refused:
        _settings(HMAC_KEYS=value)

    assert "real-secret" not in str(refused.value)


def test_well_formed_hmac_keys_start() -> None:
    keys = {"app": "hmac-secret-value"}
    assert _settings(HMAC_KEYS='{"app": "hmac-secret-value"}').hmac_keys_dict == keys


@pytest.mark.parametrize("value", ["{}", "", " "])
def test_a_dsn_only_deployment_needs_no_hmac_keys(value: str) -> None:
    assert _settings(HMAC_KEYS=value).hmac_keys_dict == {}


@pytest.mark.parametrize(
    "url",
    [
        "memcached://:redis-password@host:6379/0",
        "redis://:redis-password@host:port/0",
        "redis://:redis-password@host:99999/0",
        "unix://:redis-password@/var/run/redis.sock?db=0",
        "REDIS://:redis-password@host:6379/0",
        " redis://:redis-password@host:6379/0",
    ],
)
def test_an_unreadable_redis_url_is_refused_without_printing_it(url: str) -> None:
    with pytest.raises(ValidationError, match="REDIS_URL") as refused:
        _settings(REDIS_URL=url)

    assert "redis-password" not in str(refused.value)


@pytest.mark.parametrize(
    "url",
    [
        "redis://:?db=redis-password@127.0.0.1:6379/0",
        "redis://h/0?db=abc&x=redis-password",
        "redis://:redis-password@h/0?socket_timeout=abc",
        "redis://:redis-password@h/0?foo=bar",
        "redis://:redis-password@h/0?db=abc",
    ],
)
def test_a_redis_url_redis_py_cannot_build_a_client_from_is_refused_unprinted(
    url: str,
) -> None:
    with pytest.raises(ValidationError, match="REDIS_URL") as refused:
        _settings(REDIS_URL=url)

    assert "redis-password" not in str(refused.value)


@pytest.mark.parametrize(
    "url",
    [
        "redis://localhost",
        "redis://:p@localhost:6379/0",
        "redis://user:p@localhost:6379/0",
        "redis://[::1]:6379/2",
        "redis://h/0?socket_timeout=5",
        "redis://h/0?db=3",
        "redis://:p%40ss@h:6379/0",
        "rediss://:p@localhost:6380/1",
    ],
)
def test_a_redis_url_the_settings_accept_is_one_both_clients_read(url: str) -> None:
    accepted = _settings(REDIS_URL=url).REDIS_URL
    assert accepted == url

    storage_from_string(url)
    redis.from_url(url)


@pytest.mark.parametrize("url", [None, ""])
def test_an_unset_redis_url_starts(url: str | None) -> None:
    loaded = _settings(REDIS_URL=url)

    assert url == loaded.REDIS_URL


def test_an_unreadable_database_url_is_refused_without_printing_it() -> None:
    url = "postgresql+asyncpg://u:p@ss:db-password@h:5432/d"
    with pytest.raises(ValidationError, match="DATABASE_URL") as refused:
        _settings(DATABASE_URL=url)

    assert "db-password" not in str(refused.value)


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+asyncpg://u:pw@tail-secret@h:5432/d",
        "postgresql+asyncpg://u:pw@tail-secret@h/d",
        "postgresql+asyncpg://u:pw@tail-secret/w0rd@h:5432/d",
        "postgresql+asyncpg://u:pw@tail-secret?w0rd@h:5432/d",
        "postgresql+asyncpg://u:pw@tail-secret:1234/w0rd@h:5432/d",
        "postgresql+asyncpg://u:pw@[tail-secret]@h:5432/d",
        "postgresql+asyncpg://tail-secret/x:more@h:5432/d",
        "postgresql+asyncpg://u:1234?tail-secret@h:5432/d",
    ],
)
def test_an_unencoded_at_in_the_database_password_is_refused(url: str) -> None:
    with pytest.raises(ValidationError, match="DATABASE_URL") as refused:
        _settings(DATABASE_URL=url)

    assert "tail-secret" not in str(refused.value)


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+asyncpg://u:p%40ss%2Fword@h:5432/d",
        "postgresql+asyncpg://u@h:5432/d",
        "postgresql+asyncpg://h:5432/d",
        "postgresql+asyncpg://u:p@[::1]:5432/d",
        "postgresql+asyncpg://u:p@/d?host=/var/run/postgresql",
    ],
)
def test_a_database_url_with_its_at_in_the_authority_starts(url: str) -> None:
    assert url == _settings(DATABASE_URL=url).DATABASE_URL


@pytest.mark.parametrize(
    "query",
    ["timeout=1", "queue_class=x", "retry=3", "maint_notifications_config=x"],
)
def test_a_redis_url_either_client_cannot_use_is_refused_by_name(query: str) -> None:
    with pytest.raises(ValidationError, match="REDIS_URL") as refused:
        _settings(REDIS_URL=f"redis://:redis-secret@h:6379/0?{query}")

    assert "redis-secret" not in str(refused.value)


@pytest.mark.parametrize(
    "query",
    [
        "encoding=x",
        "encoding_errors=%00",
        "socket_timeout=nan",
        "socket_timeout=inf",
        "socket_timeout=-1",
        "socket_timeout=1e18",
        "socket_connect_timeout=1e18",
        "socket_connect_timeout=nan",
        "credential_provider=x",
        "redis_connect_func=x",
        "event_dispatcher=x",
        "socket_keepalive_options=x",
        "socket_type=x",
        "socket_read_size=-1",
        "ssl_min_version=3",
        "client_name=two%20words",
        "protocol=5",
        "%73ocket_type=x",
    ],
)
def test_a_redis_option_outside_the_known_usable_ones_is_refused(query: str) -> None:
    with pytest.raises(ValidationError, match="REDIS_URL"):
        _settings(REDIS_URL=f"rediss://h:6379/0?{query}")


@pytest.mark.parametrize(
    "url",
    [
        "redis://:pw@tail-secret/rest@h:6379/0",
        "redis://:pw@tail-secret?rest@h:6379/0",
        "redis://:pw@tail-secret#rest@h:6379/0",
        "redis://:pw@tail-secret:1234/rest@h:6379/0",
        "redis://:1234/tail-secret@h:6379/0",
        "redis://u:1234/tail-secret@h:6379/0",
    ],
)
def test_a_redis_password_with_an_at_past_the_authority_is_refused(url: str) -> None:
    with pytest.raises(ValidationError, match="REDIS_URL") as refused:
        _settings(REDIS_URL=url)

    assert "tail-secret" not in str(refused.value)


@pytest.mark.parametrize(
    "url",
    [
        "redis://h:6379/0",
        "redis://:p@ss@h:6379/0",
        "rediss://appuser:p%40ss@h:6380/0?ssl_ca_certs=/ca.pem&ssl_check_hostname=true",
        "rediss://h:6380/0?ssl_cert_reqs=required&ssl_certfile=/c.pem&ssl_keyfile=/k.pem",
        "redis://h:6379/0?socket_timeout=5&client_name=lux&health_check_interval=30",
        "redis://h:6379/0?socket_connect_timeout=0.5&protocol=3&db=2",
    ],
)
def test_a_usable_redis_url_starts(url: str) -> None:
    assert url == _settings(REDIS_URL=url).REDIS_URL


INVISIBLE = ["̀", "͏", "️", "⃝", "឴", "ᅟ", "ㅤ", "ﾠ", "⠀"]


@pytest.mark.parametrize("char", INVISIBLE)
def test_a_secret_of_marks_and_fillers_is_blank(char: str) -> None:
    with pytest.raises(ValidationError, match="DASHBOARD_SESSION_SECRET"):
        _settings(DASHBOARD_SESSION_SECRET=char * 40)
    with pytest.raises(ValidationError, match="HMAC_KEYS"):
        _settings(HMAC_KEYS='{"app": "' + char * 8 + '"}')


@pytest.mark.parametrize("padding", ["̀", "ㅤ", "\x01"])
def test_a_default_password_padded_with_invisible_characters_is_guessable(
    padding: str,
) -> None:
    with pytest.raises(ValidationError, match="DASHBOARD_PASSWORD"):
        _settings(ENVIRONMENT="production", DASHBOARD_PASSWORD="admin" + padding * 7)


def test_a_visible_secret_with_marks_starts() -> None:
    secret = "kéy-" * 10
    assert secret == _settings(DASHBOARD_SESSION_SECRET=secret).DASHBOARD_SESSION_SECRET


def test_a_refused_database_url_names_the_whole_url_not_the_password() -> None:
    with pytest.raises(ValidationError, match="percent-encode reserved characters"):
        _settings(DATABASE_URL="postgresql+asyncpg://u:p@h/d?application_name=a@b")


@pytest.mark.parametrize("escaped", ["\\u200b", "\\ufeff", "\\u00ad", "\\u0000"])
def test_an_invisible_hmac_secret_is_blank(escaped: str) -> None:
    with pytest.raises(ValidationError, match="HMAC_KEYS"):
        _settings(HMAC_KEYS='{"app": "' + escaped + '"}')


@pytest.mark.parametrize("char", ["​", "﻿", "­", "\x01"])
def test_an_invisible_session_secret_is_blank(char: str) -> None:
    with pytest.raises(ValidationError, match="DASHBOARD_SESSION_SECRET"):
        _settings(DASHBOARD_SESSION_SECRET=char * 40)


def test_a_percent_encoded_database_password_starts() -> None:
    url = "postgresql+asyncpg://u:p%40ss%3Adb-password@h:5432/d"
    loaded = _settings(DATABASE_URL=url)

    assert url == loaded.DATABASE_URL


def test_malformed_cors_origins_are_refused_without_printing_them() -> None:
    with pytest.raises(ValidationError) as refused:
        _ = _settings(CORS_ORIGINS='["https://origin-value"').cors_origins_list

    assert "origin-value" not in str(refused.value)


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
    "url",
    [
        "",
        " ",
        "analytics.example.com",
        "ftp://a.b",
        "https://",
        "http://analytics.example.com",
        "https://a example.com",
        "https://u:p@analytics.example.com",
    ],
)
def test_external_url_must_be_an_https_url_with_a_plain_host(url: str) -> None:
    with pytest.raises(ValidationError, match="EXTERNAL_URL"):
        _settings(EXTERNAL_URL=url)


@pytest.mark.parametrize(
    "password",
    [
        "password1234",
        "changeme1234",
        "admin1234567",
        "Admin!!!!!!!",
        "admin\u200b\u200b\u200b\u200b\u200b\u200b\u200b",
    ],
)
def test_a_padded_default_password_is_refused(password: str) -> None:
    """The 12-character floor alone let a default plus digits, punctuation or zero-width
    characters through (adversarial pass 4)."""
    with pytest.raises(ValidationError, match="guessable"):
        _settings(DASHBOARD_PASSWORD=password)


def test_the_username_in_another_case_is_refused() -> None:
    with pytest.raises(ValidationError, match="guessable"):
        _settings(
            DASHBOARD_USERNAME="Operations-Team", DASHBOARD_PASSWORD="operations-team"
        )


def test_the_external_url_the_app_uses_is_the_one_checked() -> None:
    settings = _settings(EXTERNAL_URL="https://analytics.example.com ")
    assert settings.EXTERNAL_URL == "https://analytics.example.com"


def test_allowed_hosts_is_required(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An environment that never sets ALLOWED_HOSTS does not start (FLEET-BUILD-DEPLOY-STANDARD
    "Trusted hosts"): there is no derived fallback."""
    monkeypatch.delenv("ALLOWED_HOSTS", raising=False)
    monkeypatch.chdir(tmp_path)  # no `.env` file to read it from either
    values = {k: v for k, v in {**REQUIRED, **SAFE}.items() if k != "ALLOWED_HOSTS"}
    with pytest.raises(ValidationError, match="ALLOWED_HOSTS"):
        Settings.model_validate(values)


@pytest.mark.parametrize("hosts", ["", " , ", "*", "analytics.example.com,*"])
def test_allowed_hosts_that_answer_every_host_or_none_are_refused(hosts: str) -> None:
    with pytest.raises(ValidationError, match="ALLOWED_HOSTS"):
        _settings(ALLOWED_HOSTS=hosts)


def test_allowed_hosts_is_a_comma_list() -> None:
    assert _settings(ALLOWED_HOSTS="a.example.com, B.example.com").ALLOWED_HOSTS == [
        "a.example.com",
        "b.example.com",
    ]


def test_no_unread_database_url_is_required() -> None:
    """DATABASE_URL is the one database setting: the old DATABASE_URL_SYNC was required, read by
    nothing, and made every operator invent a value (LUXANALYTI-85)."""
    assert "DATABASE_URL_SYNC" not in Settings.model_fields
    assert REQUIRED["DATABASE_URL"] == _settings().DATABASE_URL


def test_a_dotenv_file_with_a_retired_key_still_loads(tmp_path: Path) -> None:
    """An operator's `.env` written for 2026.10.0 still sets DATABASE_URL_SYNC: a retired key is
    ignored, as it is from the environment, not a startup failure (adversarial pass 6)."""
    env = tmp_path / ".env"
    lines = [f"{k}={v}" for k, v in {**REQUIRED, **SAFE}.items()] + [
        "DATABASE_URL_SYNC=x"
    ]
    env.write_text("\n".join(lines) + "\n")
    # EXTERNAL_URL: a key only the file sets (the suite's environment sets the database URL).
    assert SAFE["EXTERNAL_URL"] == Settings(_env_file=env).EXTERNAL_URL
