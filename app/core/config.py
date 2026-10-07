import json
from typing import Dict, List, Optional

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # App
    APP_NAME: str = "LuxAnalytics"
    APP_VERSION: str = "1.0.3"
    BUILD_TIMESTAMP: Optional[str] = None
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
    CORS_ORIGINS: Optional[str] = None  # Comma-separated or JSON list
    ALLOWED_HOSTS: Optional[str] = None  # Comma-separated

    # Rate Limiting
    RATE_LIMIT_REQUESTS: int = 100
    RATE_LIMIT_WINDOW: int = 60

    # Logging
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "json"

    # Redis
    REDIS_URL: Optional[str] = None

    # Performance
    MAX_REQUEST_SIZE: int = 10 * 1024 * 1024

    # Event Validation
    EVENT_TIMESTAMP_FUTURE_TOLERANCE: int = 60  # seconds

    # Dashboard Auth
    DASHBOARD_USERNAME: str = "admin"
    DASHBOARD_PASSWORD: str = "changeme"
    DASHBOARD_SESSION_SECRET: Optional[str] = None
    DASHBOARD_SESSION_TIMEOUT: int = 3600

    # URLs
    EXTERNAL_URL: Optional[str] = None
    INTERNAL_URL: Optional[str] = None

    # OpenTelemetry
    OTEL_EXPORTER_OTLP_ENDPOINT: Optional[str] = None
    OTEL_SERVICE_NAME: str = "luxanalytics"

    @property
    def hmac_keys_dict(self) -> Dict[str, str]:
        return json.loads(self.HMAC_KEYS)

    @property
    def cors_origins_list(self) -> List[str]:
        """Parse CORS origins from comma-separated string or JSON list."""
        if not self.CORS_ORIGINS:
            return ["*"]
        try:
            return json.loads(self.CORS_ORIGINS)
        except json.JSONDecodeError:
            return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def allowed_hosts_list(self) -> List[str]:
        if not self.ALLOWED_HOSTS:
            return ["*"]
        return [h.strip() for h in self.ALLOWED_HOSTS.split(",") if h.strip()]

    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()  # type: ignore[call-arg]
