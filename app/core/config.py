import math
import re
import string
import unicodedata
from collections.abc import Callable
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from pathlib import Path
from urllib.parse import parse_qsl, urlparse

from pydantic import (
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict
from redis import ConnectionPool
from redis.asyncio import BlockingConnectionPool
from redis.exceptions import RedisError
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from app.core.trusted_host import AllowedHosts

# Parses HMAC_KEYS, so a refusal must not print what it refused: the value holds secrets.
_STR_DICT = TypeAdapter(dict[str, str], config=ConfigDict(hide_input_in_errors=True))
# The forms both Redis clients read: redis-py checks the prefix as written, and slowapi's `limits`
# names a socket differently from redis-py, so no socket form works for both.
_REDIS_URL_PREFIXES = ("redis://", "rediss://")
_STR_LIST = TypeAdapter(list[str], config=ConfigDict(hide_input_in_errors=True))
_INVISIBLE_CATEGORIES = {"Cc", "Cf", "Cn", "Co", "Cs", "Me", "Mn"}
_FILLERS = {"ᅟ", "ᅠ", "⠀", "ㅤ", "ﾠ", "￼"}
MAX_REDIS_TIMEOUT_SECONDS = 86400


def _visible(text: str) -> str:
    """The text without what renders as nothing: controls, format characters, unassigned and
    private code points, combining marks and the blank fillers. Whitespace stays."""
    return "".join(
        c
        for c in text
        if c in string.whitespace
        or (unicodedata.category(c) not in _INVISIBLE_CATEGORIES and c not in _FILLERS)
    )


def _blank(secret: str) -> bool:
    """Nothing but whitespace and invisible characters: a key anyone can guess."""
    return not _visible(secret).strip()


def _split_after_authority(url: str) -> tuple[str, str]:
    """The URL after its scheme, split at the first of `/ ? #`: (authority, the rest)."""
    after_scheme = url.partition("://")[2]
    authority = re.split(r"[/?#]", after_scheme, maxsplit=1)[0]
    return authority, after_scheme[len(authority) :]


def _stray_at(url: str) -> bool:
    """An `@` beyond the one that ends the credentials: a password's unencoded `@`."""
    authority, rest = _split_after_authority(url)
    return authority.count("@") > 1 or "@" in rest


def _positive_number(value: str) -> bool:
    try:
        number = float(value)
    except ValueError:
        return False
    return math.isfinite(number) and number > 0


def _socket_timeout(value: str) -> bool:
    """A day at most: past about 9e9 seconds the sync client's connect raises OverflowError."""
    return _positive_number(value) and float(value) <= MAX_REDIS_TIMEOUT_SECONDS


# The URL options the app's Redis clients can use. Each client takes any other query name as a
# constructor argument, and many of those construct and then fail on first use: on the async client
# as a 500 on every ingest and /health, on the limiter's sync client as a silent per-process count.
_REDIS_URL_OPTIONS: dict[str, Callable[[str], bool]] = {
    "db": str.isdecimal,
    "socket_timeout": _socket_timeout,
    "socket_connect_timeout": _socket_timeout,
    "health_check_interval": _positive_number,
    "client_name": lambda v: re.fullmatch(r"[!-~]+", v) is not None,
    "protocol": {"2", "3"}.__contains__,
    "ssl_cert_reqs": {"none", "optional", "required"}.__contains__,
    "ssl_check_hostname": {"true", "false"}.__contains__,
    "ssl_ca_certs": bool,
    "ssl_certfile": bool,
    "ssl_keyfile": bool,
}


def _redis_options_usable(url: str) -> bool:
    return all(
        name in _REDIS_URL_OPTIONS and _REDIS_URL_OPTIONS[name](value)
        for name, value in parse_qsl(urlparse(url).query, keep_blank_values=True)
    )


def _guessable(password: str, username: str) -> bool:
    """A default or the username, bare or padded: "admin1234567" and "password!!!!" are as
    guessable as "admin". Invisible characters do not count."""
    visible = _visible(password)
    core = visible.lower().strip(string.digits + string.punctuation + string.whitespace)
    return len(visible.strip()) < MIN_PRODUCTION_PASSWORD or core in {
        *WEAK_PASSWORDS,
        username.strip().lower(),
    }


def _running_version() -> str:
    """The running version, derived — never a literal (repo.version_single_source).

    The installed distribution's metadata first; the repo-root VERSION file otherwise (the image
    installs dependencies only, with --no-root, and copies VERSION next to the app).
    """
    try:
        return package_version("luxanalytics")
    except PackageNotFoundError:
        version_file = Path(__file__).resolve().parents[2] / "VERSION"
        return version_file.read_text(encoding="utf-8").strip()


# Everything else is production: the checks fail closed, so a typo ("Production", "prd") or a new
# environment name never switches them off.
DEVELOPMENT_ENVIRONMENTS = {"dev", "development", "test"}
WEAK_PASSWORDS = {"admin", "changeme", "password", "secret"}
MIN_PRODUCTION_PASSWORD = 12
# The most events one ingest batch carries (event_schema); the ingest limits must fit one.
MAX_BATCH_EVENTS = 1000


class Settings(BaseSettings):
    # App
    APP_NAME: str = "LuxAnalytics"
    APP_VERSION: str = Field(default_factory=lambda: _running_version())
    BUILD_TIMESTAMP: str | None = None
    # The git short SHA the image was built from (Dockerfile ENV; = the OCI revision label).
    BUILD_COMMIT: str | None = None
    DEBUG: bool = False
    ENVIRONMENT: str = "production"

    # Database
    DATABASE_URL: str

    # Database Connection Pool
    DB_POOL_SIZE: int = 20
    DB_POOL_MAX_OVERFLOW: int = 40
    DB_POOL_TIMEOUT: int = 5
    DB_POOL_RECYCLE: int = 1800
    DB_POOL_PRE_PING: bool = True
    DB_POOL_RESET_ON_RETURN: str = "rollback"

    # Security
    SECRET_KEY: str = Field(min_length=32)
    HMAC_KEYS: str = "{}"

    # CORS + Hosts
    CORS_ORIGINS: str | None = None  # Comma-separated or JSON list
    # Required, no default: the Host headers this environment serves, comma-separated, set in
    # .env.<env> (FLEET-BUILD-DEPLOY-STANDARD "Trusted hosts"). Unset, empty or `*` refuses to start.
    ALLOWED_HOSTS: AllowedHosts

    # Rate Limiting
    RATE_LIMIT_REQUESTS: int = 100
    RATE_LIMIT_WINDOW: int = 60
    # Ingest, in events per INGEST_RATE_LIMIT_WINDOW, after authentication (rate_limiter
    # enforce_ingest_limits). Per client address and per device: at least MAX_BATCH_EVENTS, or a
    # full batch is refused for good. Per app: a ceiling above one address's worth (device ids
    # are client-chosen, so one address could otherwise fill it), against a flood spread over many.
    IP_RATE_LIMIT_EVENTS: int = 6000
    DEVICE_RATE_LIMIT_EVENTS: int = 1800
    APP_RATE_LIMIT_EVENTS: int = 60000
    INGEST_RATE_LIMIT_WINDOW: int = 60
    # POST /login, per client address (slowapi syntax). A login cannot key on identity: the
    # credential is what is being verified (FLEET-RATE-LIMIT-STANDARD §1.3).
    LOGIN_RATE_LIMIT: str = "5/minute"
    # Peers whose X-Forwarded-For is believed (comma-separated CIDRs): the app is reachable only
    # through nginx on the docker network. See app/core/client_ip.py for the trust model.
    TRUSTED_PROXIES: str = "127.0.0.0/8,::1/128,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"

    # Logging
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "json"

    # Redis
    REDIS_URL: str | None = None

    # Performance
    MAX_REQUEST_SIZE: int = 10 * 1024 * 1024

    # Event Validation
    EVENT_TIMESTAMP_FUTURE_TOLERANCE: int = 60  # seconds

    # Dashboard Auth
    DASHBOARD_USERNAME: str = "admin"
    # Required, no default: a missing env var must stop the app, never fall back to a guessable
    # credential on the internet-facing dashboard.
    DASHBOARD_PASSWORD: str = Field(min_length=1)
    DASHBOARD_SESSION_SECRET: str | None = Field(default=None, min_length=32)
    DASHBOARD_SESSION_TIMEOUT: int = 3600

    # URLs
    EXTERNAL_URL: str | None = None
    INTERNAL_URL: str | None = None

    # OpenTelemetry
    OTEL_EXPORTER_OTLP_ENDPOINT: str | None = None
    OTEL_SERVICE_NAME: str = "luxanalytics"

    @property
    def hmac_keys_dict(self) -> dict[str, str]:
        """HMAC_KEYS as {key id: secret}; a value that is not that shape fails loudly here."""
        return _STR_DICT.validate_json(self.HMAC_KEYS)

    @property
    def cors_origins_list(self) -> list[str]:
        """Origins allowed cross-origin, credentials included: CORS_ORIGINS as a comma list or JSON.

        Unset means NO cross-origin access (fail closed). The dashboard is same-origin and the
        iOS SDK is not a browser, so neither needs CORS. A wildcard here would let any site make
        credentialed requests with the dashboard session.
        """
        if not self.CORS_ORIGINS:
            return []
        if self.CORS_ORIGINS.lstrip().startswith("["):
            return _STR_LIST.validate_json(self.CORS_ORIGINS)
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @field_validator("EXTERNAL_URL")
    @classmethod
    def external_url_stripped(cls, v: str | None) -> str | None:
        """The value the app uses is the value the production check judged: a quoted trailing
        space would otherwise reach every DSN."""
        if v is None:
            return None
        return v.strip() or None

    @field_validator("REDIS_URL")
    @classmethod
    def redis_url_readable(cls, v: str | None) -> str | None:
        """A URL the Redis clients cannot read crashes the import with the whole URL, password
        included, in its traceback; refused here, the startup log names only the setting."""
        if not v:
            return v
        if not v.startswith(_REDIS_URL_PREFIXES):
            raise ValueError("REDIS_URL must be a redis:// or rediss:// URL")
        if "@" in _split_after_authority(v)[1]:
            raise ValueError(
                "REDIS_URL is not a readable URL (percent-encode its password)"
            )
        try:
            if not _redis_options_usable(v):
                raise ValueError("unusable option")
            # Each client's own parser and constructor: the app's async pool, and the sync pool
            # slowapi's limiter opens, which falls back to per-process counts if it cannot connect.
            pool = BlockingConnectionPool.from_url(v)
            _ = pool.connection_class(**pool.connection_kwargs)
            _ = ConnectionPool.from_url(v).make_connection()
        except AttributeError, ValueError, TypeError, RedisError:
            raise ValueError("REDIS_URL is not a readable URL") from None
        return v

    @field_validator("DATABASE_URL")
    @classmethod
    def database_url_readable(cls, v: str) -> str:
        """A URL SQLAlchemy cannot read (an unencoded `@` or `:` in the password) crashes the import
        with part of the password in its traceback, and an unencoded `@` can parse with part of the
        password as the host, database or query; refused here, the log names only the setting."""
        try:
            readable = not _stray_at(v) and bool(make_url(v))
        except ArgumentError, ValueError:
            readable = False
        if not readable:
            raise ValueError(
                "DATABASE_URL is not a readable URL (percent-encode reserved characters)"
            )
        return v

    @field_validator("DASHBOARD_SESSION_SECRET", mode="before")
    @classmethod
    def blank_session_secret_is_unset(cls, v: object) -> object:
        """An empty `KEY=` line means unset, as it does for REDIS_URL. Whitespace that fills the
        length is a key anyone can guess."""
        if v == "":
            return None
        if isinstance(v, str) and _blank(v):
            raise ValueError("DASHBOARD_SESSION_SECRET must not be blank")
        return v

    @field_validator("HMAC_KEYS", mode="before")
    @classmethod
    def blank_hmac_keys_are_none(cls, v: object) -> object:
        """An empty `KEY=` line means no keys: a DSN-only deployment needs none."""
        return "{}" if isinstance(v, str) and not v.strip() else v

    @model_validator(mode="after")
    def hmac_keys_readable(self) -> Settings:
        """HMAC_KEYS is parsed on each signed request, so a malformed value is refused at startup,
        not at the first request, and so is a blank secret; the refusal names the setting and none of
        its value."""
        try:
            keys = _STR_DICT.validate_json(self.HMAC_KEYS)
        except ValidationError:
            raise ValueError(
                'HMAC_KEYS must be a JSON object of {"<key id>": "<secret>"}'
            ) from None
        if any(_blank(secret) for secret in keys.values()):
            raise ValueError("HMAC_KEYS must not hold a blank secret")
        return self

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT.strip().lower() not in DEVELOPMENT_ENVIRONMENTS

    @model_validator(mode="after")
    def ingest_limits_fit(self) -> Settings:
        """A budget smaller than one batch refuses that batch on every retry; a window under a
        second empties every budget on each check; a ceiling one address can fill is a budget
        anyone holding the public DSN can spend."""
        for name in ("IP_RATE_LIMIT_EVENTS", "DEVICE_RATE_LIMIT_EVENTS"):
            if getattr(self, name) < MAX_BATCH_EVENTS:
                raise ValueError(
                    f"{name} must be at least MAX_BATCH_EVENTS ({MAX_BATCH_EVENTS})"
                )
        if self.INGEST_RATE_LIMIT_WINDOW < 1:
            raise ValueError("INGEST_RATE_LIMIT_WINDOW must be at least 1 second")
        if self.APP_RATE_LIMIT_EVENTS <= self.IP_RATE_LIMIT_EVENTS:
            raise ValueError(
                "APP_RATE_LIMIT_EVENTS must be above IP_RATE_LIMIT_EVENTS: one client address "
                "must not be able to fill the app's ceiling"
            )
        return self

    @model_validator(mode="after")
    def request_limits_fit(self) -> Settings:
        """A window under a second empties the request limits' windows on each check, and a
        budget under one refuses everything: neither limits anything as configured."""
        if self.RATE_LIMIT_WINDOW < 1:
            raise ValueError("RATE_LIMIT_WINDOW must be at least 1 second")
        if self.RATE_LIMIT_REQUESTS < 1:
            raise ValueError("RATE_LIMIT_REQUESTS must be at least 1")
        return self

    @model_validator(mode="after")
    def production_is_safe(self) -> Settings:
        """Production refuses to start on a setting that is only safe in development: a missing
        DASHBOARD_SESSION_SECRET (a per-process key signs every restart out and splits workers),
        a guessable dashboard password, DEBUG (it serves /docs and /openapi.json), or no usable
        https EXTERNAL_URL (DSNs are built from it)."""
        if not self.is_production:
            return self
        if _guessable(self.DASHBOARD_PASSWORD, self.DASHBOARD_USERNAME):
            raise ValueError(
                "DASHBOARD_PASSWORD is a default or guessable value in production "
                f"(it needs {MIN_PRODUCTION_PASSWORD}+ characters)"
            )
        if not self.DASHBOARD_SESSION_SECRET:
            raise ValueError(
                "DASHBOARD_SESSION_SECRET is required in production (32+ characters)"
            )
        if self.DEBUG:
            raise ValueError("DEBUG must be false in production")
        # DSNs are built as https://PUBLIC_ID@<netloc>/….
        url = urlparse(self.EXTERNAL_URL or "")
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username is not None
            or any(c.isspace() for c in url.netloc)
        ):
            raise ValueError(
                "EXTERNAL_URL must be an https URL with a plain host in production"
            )
        return self

    # extra="ignore": a key the settings no longer declare (DATABASE_URL_SYNC, retired by
    # LUXANALYTI-85) is ignored from a `.env` file exactly as it already is from the environment,
    # so a config written for an older release still starts this one.
    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=True,
        extra="ignore",
        # A refused value can be a secret, and the refusal goes to the startup log.
        hide_input_in_errors=True,
    )


settings = Settings()
