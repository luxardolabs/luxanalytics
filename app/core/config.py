from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from pathlib import Path
from urllib.parse import urlparse

from pydantic import Field, TypeAdapter, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_STR_DICT = TypeAdapter(dict[str, str])
_STR_LIST = TypeAdapter(list[str])


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
    DATABASE_URL_SYNC: str

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
    ALLOWED_HOSTS: str | None = None  # Comma-separated

    # Rate Limiting
    RATE_LIMIT_REQUESTS: int = 100
    RATE_LIMIT_WINDOW: int = 60
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

    @property
    def allowed_hosts_list(self) -> list[str]:
        """Host headers the app answers: ALLOWED_HOSTS (comma list) when set.

        Unset, it fails closed to EXTERNAL_URL's host plus localhost (the container health check)
        instead of accepting any Host header.
        """
        if self.ALLOWED_HOSTS:
            return [h.strip() for h in self.ALLOWED_HOSTS.split(",") if h.strip()]
        hosts = ["localhost", "127.0.0.1"]
        external_host = (
            urlparse(self.EXTERNAL_URL).hostname if self.EXTERNAL_URL else None
        )
        if external_host:
            hosts.insert(0, external_host)
        return hosts

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT.strip().lower() not in DEVELOPMENT_ENVIRONMENTS

    @model_validator(mode="after")
    def production_is_safe(self) -> Settings:
        """Production refuses to start on a setting that is only safe in development
        (LUXANALYTI-22): a guessable dashboard password, DEBUG (it serves /docs and /openapi.json),
        or no usable EXTERNAL_URL (DSNs and the allowed host derive from it)."""
        if not self.is_production:
            return self
        password = self.DASHBOARD_PASSWORD.strip()
        if (
            len(password) < MIN_PRODUCTION_PASSWORD
            or password.lower() in WEAK_PASSWORDS
            or password == self.DASHBOARD_USERNAME
        ):
            raise ValueError(
                "DASHBOARD_PASSWORD is a default or guessable value in production "
                f"(it needs {MIN_PRODUCTION_PASSWORD}+ characters)"
            )
        if self.DEBUG:
            raise ValueError("DEBUG must be false in production")
        url = urlparse((self.EXTERNAL_URL or "").strip())
        if url.scheme not in ("http", "https") or not url.hostname:
            raise ValueError(
                "EXTERNAL_URL must be an http(s) URL with a host in production"
            )
        return self

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=True)


settings = Settings()
