import json
from urllib.parse import urlparse

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # App
    APP_NAME: str = "LuxAnalytics"
    APP_VERSION: str = "1.0.3"
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
    DASHBOARD_PASSWORD: str = "changeme"
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
        return json.loads(self.HMAC_KEYS)

    @property
    def cors_origins_list(self) -> list[str]:
        """Origins allowed cross-origin, credentials included: CORS_ORIGINS as a comma list or JSON.

        Unset means NO cross-origin access (fail closed). The dashboard is same-origin and the
        iOS SDK is not a browser, so neither needs CORS. A wildcard here would let any site make
        credentialed requests with the dashboard session.
        """
        if not self.CORS_ORIGINS:
            return []
        try:
            return json.loads(self.CORS_ORIGINS)
        except json.JSONDecodeError:
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

    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()  # type: ignore[call-arg]
