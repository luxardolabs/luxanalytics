# Analytics Event Collector API

A production-ready FastAPI application for collecting and analyzing analytics events from iOS apps and other clients.

## Features

### Core Functionality
- 🚀 **FastAPI** with async/await support
- 🗄️ **PostgreSQL** with SQLAlchemy, connection pooling, and optimized indexes
- 🔐 **Triple Authentication**: 
  - HMAC-SHA256 signatures with timestamp validation (primary)
  - DSN-style endpoints (Sentry-compatible format)
  - API keys (fallback)
- 📊 **Event Validation** with Pydantic v2 schemas
- 🗜️ **Compression Support**: Automatic zlib/deflate decompression
- 📦 **Batch Processing**: Bulk insert optimization for high throughput
- 🌐 **Analytics Dashboard**: Full web UI with 10+ visualization views

### Production Features
- 🛡️ **Rate Limiting**: Redis-based distributed limiting with in-memory fallback
- 📝 **Structured Logging**: JSON logs with correlation IDs via structlog
- 📈 **Observability**: Prometheus metrics + OpenTelemetry tracing
- 🔄 **Connection Pooling**: Database pool monitoring with circuit breakers
- 🚦 **Request Size Limiting**: Configurable payload size limits (default 10MB)
- ⚡ **Performance**: Database indexes, bulk inserts, connection pooling
- 🐳 **Docker**: Multi-stage builds with health checks and non-root user
- 🧪 **Testing**: Async test suite with pytest

### Security
- **HMAC Authentication**: SHA256 signatures with timestamp validation (configurable tolerance)
- **DSN Authentication**: Sentry-style endpoints with Basic auth
- **Web Dashboard Auth**: Session-based username/password authentication
- **Replay Protection**: Timestamp-based request validation
- **Request Validation**: Size limits, compression bomb protection
- **Error Handling**: Graceful failures with circuit breakers

## Quick Start

### 1. Setup Environment

```bash
# Clone the repository
git clone <repository-url>
cd ll_analytics

# Generate API keys and HMAC secrets for your apps
python scripts/generate_keys.py app1 app2 app3
python scripts/generate_hmac_secrets.py app1 app2 app3

# Create .env file with generated keys
cp .env.example .env
# Update .env with the generated API_KEYS and HMAC_KEYS
```

### 2. Run with Docker Compose

```bash
# Development environment (with hot reload)
docker compose -f compose.dev.yaml up

# Production environment
docker compose -f compose.prod.yaml up -d

# Rebuild after dependency changes
docker compose -f compose.dev.yaml up --build

# View logs
docker compose logs -f ll-analytics
```

### 3. Run Locally (Development)

```bash
# Install dependencies
cd src
pip install -r ../requirements.txt

# Run database migrations
./scripts/run_migrations.sh

# Run the application
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Run tests
pytest
```

## Analytics Dashboard

The application includes a comprehensive web dashboard accessible at `http://localhost:8000/dashboard`

### Dashboard Authentication
- Default credentials: `admin` / `admin` (change in production!)
- Configure via environment variables:
  - `DASHBOARD_USERNAME`
  - `DASHBOARD_PASSWORD`
  - `DASHBOARD_SESSION_SECRET`
  - `DASHBOARD_SESSION_TIMEOUT`

### Dashboard Features
- **Apps Management**: Full CRUD operations for multi-tenant app configuration
- **Apps Overview**: View all apps with event counts and last activity
- **Events Timeline**: Interactive time-series visualization
- **Event Details**: Deep dive into individual events with metadata
- **Search & Filtering**: Find events by name, user, session, or metadata
- **Analytics Views**:
  - Device Analytics: Device models, OS versions, app versions
  - Feature Usage: Track feature adoption and usage patterns
  - Error Analytics: Monitor errors and exceptions
  - Performance Metrics: API latencies and response times
  - User Journey: Track user paths through the application
  - JSON Analyzer: Explore metadata fields with frequency analysis
  - **Feedback Analytics**: Dedicated view for user feedback with:
    - Summary statistics and categorization
    - Contact information for follow-up
    - Device and user context
    - Custom detail view for feedback events

## API Endpoints

### Core Endpoints
- `GET /` - Redirects to dashboard
- `GET /dashboard` - Main analytics dashboard
- `GET /health` - Health check with component status
- `GET /metrics` - Prometheus metrics endpoint

### Event Collection API

#### Primary Endpoint with HMAC Authentication
```bash
# Example with HMAC signature (required for production)
TIMESTAMP=$(date +%s)
KEY_ID="your_app_id"
HMAC_SECRET="your_hmac_secret"
PAYLOAD='{"name": "screen_view", "timestamp": "2024-01-01T12:00:00", "user_id": "user123"}'

# Calculate HMAC signature
SIGNATURE=$(echo -n "${PAYLOAD}${TIMESTAMP}" | openssl dgst -sha256 -hmac "${HMAC_SECRET}" | cut -d' ' -f2)

curl -X POST "http://localhost:8000/api/v1/events/" \
  -H "Content-Type: application/json" \
  -H "X-HMAC-Signature: ${SIGNATURE}" \
  -H "X-Key-ID: ${KEY_ID}" \
  -H "X-Timestamp: ${TIMESTAMP}" \
  -d "${PAYLOAD}"
```

#### DSN-Style Endpoint (Sentry-compatible)
```bash
# Using DSN format: https://PUBLIC_ID@host/api/v1/events/PROJECT_ID
DSN="https://abc123@analytics.example.com/api/v1/events/proj123"

# Extract components
PUBLIC_ID="abc123"
PROJECT_ID="proj123"

# Send event with Basic Auth
curl -X POST "http://localhost:8000/api/v1/events/${PROJECT_ID}" \
  -H "Content-Type: application/json" \
  -H "Authorization: Basic $(echo -n ${PUBLIC_ID}: | base64)" \
  -d '{"name": "test_event", "timestamp": "2024-01-01T12:00:00"}'
```

#### Compression Support
The API automatically handles compressed payloads:
- Requests with `Content-Encoding: deflate` header are automatically decompressed
- Supports zlib/deflate compression (raw deflate format)
- HMAC signature must be calculated on the compressed payload
- Typically used by clients for payloads ≥ 1KB

#### Stats Endpoint
```bash
# Stats endpoint also requires HMAC authentication
curl -X GET "http://localhost:8000/api/v1/events/stats" \
  -H "X-HMAC-Signature: ${SIGNATURE}" \
  -H "X-Key-ID: ${KEY_ID}" \
  -H "X-Timestamp: ${TIMESTAMP}"
```

### Event Formats

#### Single Event
```json
{
  "name": "page_view",
  "timestamp": "2024-01-01T12:00:00",
  "user_id": "user123",
  "session_id": "session456",
  "metadata": {"page": "/home", "referrer": "google"}
}
```

#### Batch Events (Optimized for bulk insert)
```json
{
  "events": [
    {
      "name": "page_view",
      "timestamp": "2024-01-01T12:00:00",
      "user_id": "user123",
      "metadata": {"page": "/home"}
    },
    {
      "name": "button_click",
      "timestamp": "2024-01-01T12:01:00",
      "user_id": "user123",
      "metadata": {"button": "signup"}
    }
  ]
}
```

**Note**: Batch requests now use bulk insert for optimal performance. Maximum batch size is 1000 events per request.

## Configuration

All configuration is managed through environment variables:

```bash
# Database (use psycopg3 driver for better stability)
DATABASE_URL=postgresql+psycopg://user:password@localhost:5432/analytics
DATABASE_URL_SYNC=postgresql://user:password@localhost:5432/analytics

# Database Connection Pool Settings (Production Optimized)
DB_POOL_SIZE=100                   # Increased for high concurrency
DB_POOL_MAX_OVERFLOW=0             # Disabled to prevent connection storms
DB_POOL_TIMEOUT=3                  # Fail fast strategy
DB_POOL_RECYCLE=600               # 10 minutes for stable connections
DB_POOL_PRE_PING=true             # Test connections before use
DB_POOL_RESET_ON_RETURN=rollback  # Reset connection state

# Redis Configuration (for distributed rate limiting)
REDIS_URL=redis://localhost:6379/0  # Optional, falls back to in-memory

# Security
SECRET_KEY=your-secret-key
HMAC_KEYS={"app1": "hmac-secret-1", "app2": "hmac-secret-2"}

# Rate Limiting
RATE_LIMIT_REQUESTS=1000           # Increased for production
RATE_LIMIT_WINDOW=60               # Per minute

# Request Limits
MAX_REQUEST_SIZE=10485760          # 10MB maximum request size

# Event Timestamp Validation
EVENT_TIMESTAMP_FUTURE_TOLERANCE=300  # Max seconds events can be in future (default: 300)

# Web Dashboard Authentication
DASHBOARD_USERNAME=admin           # Dashboard login username (default: admin)
DASHBOARD_PASSWORD=changeme        # Dashboard login password (CHANGE THIS!)
DASHBOARD_SESSION_SECRET=          # Session encryption key (auto-generated if empty)
DASHBOARD_SESSION_TIMEOUT=3600     # Session timeout in seconds (default: 1 hour)

# Performance Settings (Flags exist but not all implemented)
ENABLE_ASYNC_PROCESSING=false      # Flag exists, no queue implementation yet
UVLOOP_ENABLED=true               # Flag exists, uvloop installed but not used
USE_ORJSON=true                   # Flag exists, orjson installed but not used

# Logging
LOG_LEVEL=INFO                    # INFO for production
LOG_FORMAT=json
```

## Database Schema

The `events` table includes:
- `id`: UUID primary key
- `app_id`: Application identifier
- `name`: Event name (required)
- `timestamp`: Event timestamp (required)
- `user_id`: User identifier (optional)
- `session_id`: Session identifier (optional)
- `metadata`: JSON metadata (optional)
- `received_at`: Server timestamp

## Development

### Running Tests
```bash
pytest tests/ -v
```

### Creating New Migrations
```bash
alembic revision --autogenerate -m "Description of changes"
alembic upgrade head
```

### Generating New Keys
```bash
python scripts/generate_keys.py myapp1 myapp2
```

## Authentication

The API uses HMAC-SHA256 authentication for all endpoints:

1. **Required Headers**:
   - `X-HMAC-Signature`: HMAC-SHA256 signature in hex format
   - `X-Key-ID`: Your application ID
   - `X-Timestamp`: Unix timestamp (must be within 5 minutes)

2. **Signature Calculation**:
   - For uncompressed requests: `HMAC-SHA256(payload + timestamp, secret)`
   - For compressed requests: `HMAC-SHA256(compressed_payload + timestamp, secret)`

3. **Compression**:
   - Automatic decompression for requests with `Content-Encoding: deflate`
   - Supports raw deflate format (as used by iOS NSData.compressed)
   - HMAC is always calculated on the compressed payload when compression is used

## Production Deployment

### 1. **High-Performance Configuration**
For handling millions of requests, use the optimized production settings:

```bash
# Copy optimized production config
cp .env.prod.optimized .env.prod

# Key settings for high load:
DB_POOL_SIZE=100              # Support 100 concurrent connections
DB_POOL_MAX_OVERFLOW=0        # No overflow to prevent connection storms
DB_POOL_TIMEOUT=3             # Fail fast under load
REDIS_URL=redis://redis:6379  # Enable distributed rate limiting
MAX_REQUEST_SIZE=10485760     # 10MB request limit
UVLOOP_ENABLED=true           # Better async performance
```

### 2. **PostgreSQL Optimization**
Apply the PostgreSQL configuration for high load:

```bash
# Copy to PostgreSQL config directory
cp postgres-optimization.conf /etc/postgresql/conf.d/

# Key settings:
max_connections = 500
shared_buffers = 4GB          # 25% of RAM
effective_cache_size = 12GB   # 75% of RAM
```

### 3. **Build and Deploy**
```bash
# Build with version tag (extracts version from directory structure)
./build.sh                    # Builds with version tag only (e.g., 2025.7.13)
./build.sh latest            # Also tags as 'latest'
./build.sh --private-registry # Push to private registry
./build.sh --no-cache        # Build without cache

# The build script:
# - Extracts version from path: <builds>/YYYY/MM/DD → YYYY.MM.DD
# - Captures build timestamp in ISO 8601 format
# - Passes both as Docker build arguments
# - Version and timestamp are displayed in the app (login page, dashboard, /health endpoint)

# Deploy with Docker Compose
docker-compose -f compose.prod.yaml up -d
```

### 4. **Run Migrations and Indexes**
```bash
# Run migrations
docker-compose exec app ./scripts/run_migrations.sh

# Apply performance indexes
docker-compose exec app alembic upgrade head
```

### 5. **Health Monitoring**
The enhanced `/health` endpoint now includes:
- Database connectivity status
- Redis connectivity status
- Connection pool metrics

```bash
curl http://localhost:8000/health
```

### 6. **Metrics and Monitoring**
Access Prometheus metrics at `/metrics` endpoint for:
- Connection pool utilization
- Rate limit statistics
- Request processing times
- Event insertion performance

## Monitoring

The application includes:
- **Structured Logging**: Request/response details with correlation IDs
- **Request Timing**: Middleware tracks processing time
- **Health Check**: Enhanced endpoint with component status
- **Rate Limiting**: Redis-based with headers and metrics
- **Connection Pool**: Real-time metrics and circuit breaker
- **Prometheus Metrics**: Export endpoint at `/metrics`
- **Request Size Tracking**: Monitors and limits payload sizes

## Troubleshooting

### Common Issues

1. **"Invalid compressed data" errors**:
   - Ensure client is sending with `Content-Encoding: deflate` header
   - Verify compression format (raw deflate vs zlib with headers)
   - Check HMAC is calculated on compressed payload

2. **Database connection issues**:
   - **"password authentication failed" errors** (usually stale connections):
     - This is NOT actually a password issue - it's stale connections in the pool
     - Switch to psycopg3 driver: `postgresql+psycopg://` instead of `postgresql+asyncpg://`
     - Set `DB_POOL_RECYCLE=120` (or less than your network/DB timeout)
     - Ensure `DB_POOL_PRE_PING=true` is enabled
     - Apply PostgreSQL config with proper TCP keepalive settings
     - Check cloud provider network timeouts (AWS: 350s, GCP: 600s)
   - Adjust pool settings based on load:
     - `DB_POOL_SIZE` - concurrent connections needed
     - `DB_POOL_MAX_OVERFLOW` - burst capacity
     - `DB_POOL_TIMEOUT` - how long to wait for connection

3. **HMAC authentication failures**:
   - Verify timestamp is within 5-minute window
   - Ensure signature is calculated correctly (payload + timestamp)
   - Check `HMAC_KEYS` configuration matches client

4. **Rate limiting issues**:
   - Check Redis connectivity for distributed rate limiting
   - Monitor rate limit headers: `X-RateLimit-Remaining`, `X-RateLimit-Reset`
   - Adjust `RATE_LIMIT_REQUESTS` and `RATE_LIMIT_WINDOW`
   - Different limits can be set per app_id in code

5. **High load performance**:
   - Ensure `DB_POOL_SIZE` matches expected concurrent connections
   - Set `DB_POOL_MAX_OVERFLOW=0` to prevent connection storms
   - Enable `UVLOOP_ENABLED=true` for better async performance
   - Monitor `/metrics` endpoint for pool utilization

6. **Request too large errors**:
   - Adjust `MAX_REQUEST_SIZE` for larger payloads
   - Default is 10MB, suitable for most batch operations
   - Consider splitting very large batches into multiple requests

## Implementation Status

### ✅ Fully Implemented
- **Event Collection**: Single and batch submission with bulk insert optimization
- **Security**: HMAC authentication with timestamp validation, API key fallback
- **Compression**: Request decompression (zlib/deflate)
- **Rate Limiting**: Redis-based with in-memory fallback
- **Database**: Async PostgreSQL with connection pooling and indexes
- **Monitoring**: Prometheus metrics, OpenTelemetry, structured logging
- **Dashboard**: Full analytics web UI with 10+ visualization views
- **Error Handling**: Circuit breakers, graceful degradation
- **Docker**: Multi-stage builds, health checks, production-ready

### ⚠️ Not Yet Implemented
- **CI/CD Pipeline**: No automated testing or deployment
- **Async Processing**: Configuration flag exists but no queue implementation
- **Response Compression**: Only request decompression works
- **Data Retention**: No automatic cleanup or archival
- **Application Caching**: Redis only used for rate limiting
- **Performance Flags**: UVLOOP_ENABLED and USE_ORJSON not actually used

### Recent Fixes (2025-07-05 & 2025-07-06)
- **Critical**: Fixed stale PostgreSQL connections causing fake "password authentication failed" errors
  - Switched from asyncpg to psycopg3 driver for better connection handling
  - Simplified database session management to match proven patterns
  - Added explicit commit/rollback logic to session management
- **Model Loading**: Fixed critical bug where App model wasn't loaded before migrations
  - Models must be imported at module level to ensure registration with SQLAlchemy Base
  - This was causing `project_id` column to be missing from apps table
- **Authentication**: Implemented web dashboard session-based authentication
  - API endpoints remain unchanged (HMAC/DSN auth)
  - Configure via DASHBOARD_USERNAME, DASHBOARD_PASSWORD environment variables
- **Timestamp Validation**: Made event timestamp tolerance configurable
  - Configure via EVENT_TIMESTAMP_FUTURE_TOLERANCE (default: 300 seconds)
  - Prevents legitimate events from being rejected due to clock skew
- **Middleware**: Fixed ClientDisconnect exceptions during request streaming
  - Added proper exception handling for client disconnections
  - Returns 499 status code when clients disconnect mid-request
- **Dependencies**: Fixed missing packages (OpenTelemetry exporter, itsdangerous)
- **Deprecations**: Updated Pydantic v2 validators (@validator → @field_validator)
- **Dashboard**: Fixed template errors when performance data is empty

## Production Deployment Notes

### Environment Variable Requirements
The following environment variables have been added or modified for production:
- `EVENT_TIMESTAMP_FUTURE_TOLERANCE`: Set to 300 (5 minutes) to handle client/server clock skew
- `DASHBOARD_USERNAME`: Web dashboard login username (default: admin)
- `DASHBOARD_PASSWORD`: Web dashboard login password (MUST be changed from default)
- `DASHBOARD_SESSION_SECRET`: Session encryption key (generate with `./scripts/generate_session_secret.sh`)
- `DASHBOARD_SESSION_TIMEOUT`: Session timeout in seconds (default: 3600)

### Security Updates
- Web dashboard now requires authentication (separate from API authentication)
- Session secrets should be generated using the provided script for production
- All compose files have been updated with new authentication variables

### Database Driver Change
- **IMPORTANT**: Database URLs must use `postgresql+psycopg://` instead of `postgresql+asyncpg://`
- This change resolves stale connection issues that manifest as authentication errors
- Both `DATABASE_URL` and `DATABASE_URL_SYNC` must be updated

## License

MIT License
