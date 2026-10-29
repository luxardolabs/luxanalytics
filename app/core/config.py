import string
import unicodedata
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from pathlib import Path
from urllib.parse import urlparse

from pydantic import Field, TypeAdapter, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.trusted_host import AllowedHosts

_STR_DICT = TypeAdapter(dict[str, str])
_STR_LIST = TypeAdapter(list[str])


def _guessable(password: str, username: str) -> bool:
    """A default or the username, bare or padded: "admin1234567" and "password!!!!" are as
    guessable as "admin". Invisible format characters (zero-width spaces) do not count."""
    visible = "".join(c for c in password if unicodedata.category(c) != "Cf")
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
    SECRET_KEY: str
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
    DASHBOARD_SESSION_SECRET: str | None = None
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
        """Production refuses to start on a setting that is only safe in development
        (LUXANALYTI-22): a guessable dashboard password, DEBUG (it serves /docs and /openapi.json),
        or no usable https EXTERNAL_URL (DSNs are built from it)."""
        if not self.is_production:
            return self
        if _guessable(self.DASHBOARD_PASSWORD, self.DASHBOARD_USERNAME):
            raise ValueError(
                "DASHBOARD_PASSWORD is a default or guessable value in production "
                f"(it needs {MIN_PRODUCTION_PASSWORD}+ characters)"
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
        env_file=".env", case_sensitive=True, extra="ignore"
    )


settings = Settings()
