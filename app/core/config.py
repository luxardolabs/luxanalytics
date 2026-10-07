from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from pathlib import Path
from urllib.parse import urlparse

from pydantic import Field, TypeAdapter
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


class Settings(BaseSettings):
    # App
    APP_NAME: str = "LuxAnalytics"
    APP_VERSION: str = Field(default_factory=lambda: _running_version())
    BUILD_TIMESTAMP: str | None = None
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

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=True)


settings = Settings()
