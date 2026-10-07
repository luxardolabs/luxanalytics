# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [2025.7.13] - 2025-07-13

### Added
- **Build Version and Timestamp Tracking**
  - Docker build now captures version from directory structure (YYYY.MM.DD format)
  - Build timestamp captured in ISO 8601 format at build time
  - Version and build timestamp displayed on login page and sidebar
  - Health endpoint `/health` returns both version and build_timestamp
  - Optimized Dockerfile to preserve cache when only timestamp changes
- **Enhanced Feedback Event UI**
  - Created specialized view for `feedback_submitted` events
  - Custom feedback detail page with organized sections
  - Feedback analytics dashboard with comprehensive metrics
  - Visual indicators for feedback events in event list
  - Context-aware back button navigation
- **Feedback Analytics Tab**
  - Dedicated analytics view for user feedback
  - Summary statistics (total, with email, average length, premium users)
  - Feedback categorization with visual breakdown
  - Top features and screens receiving feedback
  - Recent feedback list with quick access to details

### Changed
- **Removed All Inline Styles**
  - Migrated all inline CSS to `dashboard.css`
  - Login page now uses CSS classes instead of inline styles
  - Feedback templates use semantic CSS classes
  - Consistent styling approach across all pages
- **Improved Docker Build Caching**
  - Moved VERSION and BUILD_TIMESTAMP ARGs to end of Dockerfile
  - Expensive operations (dependencies, code copy) remain cached
  - Only final layers rebuild when timestamp changes
- **Version Display**
  - Changed default APP_VERSION from "1.0.0" to "dev"
  - All pages now show actual build version
  - Version passed to all templates using base.html

### Fixed
- **Back Button Navigation**
  - Back button in event details now returns to correct origin page
  - Feedback events opened from Feedback Analytics return there
  - Events opened from Events tab return to Events tab
  - Improved navigation flow in HTMX single-page app

### TODO
- Set up CI/CD pipeline with GitHub Actions
- Implement async background processing for heavy analytics
- Add response compression middleware
- Implement data retention and archival policies
- Add comprehensive load testing
- Add NTP monitoring for server clock drift
- Reduce EVENT_TIMESTAMP_FUTURE_TOLERANCE to 60 seconds after NTP verification
- Implement app-level caching for analytics queries
- Actually enable uvloop and orjson optimizations
- Add automated backup strategy for PostgreSQL
- Implement API versioning strategy
- Add webhook notifications for critical events
- Create admin CLI for user management

## [2025.7.6] - 2025-07-06

### Added
- **Web Dashboard Authentication**: Session-based authentication for dashboard
  - Username/password login for web UI
  - Configurable credentials via environment variables
  - Session timeout support
  - API endpoints remain unchanged (still use HMAC/DSN auth)
- **DSN-style Authentication**: Sentry-compatible endpoints
  - Support for `/api/v1/events/{project_id}` pattern
  - Basic auth with public_id as username
  - Apps now have `project_id` field for routing
- **Configurable Timestamp Validation**: EVENT_TIMESTAMP_FUTURE_TOLERANCE
  - Default 300 seconds (5 minutes) to handle clock skew
  - Prevents legitimate events from being rejected
  - Should be reduced to 60 seconds after NTP verification

### Fixed
- **Critical Model Loading Bug**: App model wasn't loaded before migrations
  - Models must be imported at module level in main.py
  - This was causing `project_id` column to be missing from apps table
  - Fixed race condition with uvicorn --reload during development
- **Database URL Format**: Updated compose files to use psycopg3
  - Changed from `postgresql+asyncpg://` to `postgresql+psycopg://`
  - Ensures consistency with database.py configuration

### Changed
- **Pydantic V2 Compatibility**: Updated validators
  - Changed from @validator to @field_validator
  - Future-proofs code for Pydantic v2
- **Dependencies**: Added missing packages
  - itsdangerous (required for SessionMiddleware)
  - opentelemetry-exporter-otlp (was missing)

### Security
- Dashboard now requires authentication (default: admin/admin)
- API endpoints maintain existing HMAC/DSN authentication
- Session cookies use secure settings

## [2025.7.5] - 2025-07-05

### Fixed
- **Critical**: Fixed stale PostgreSQL connections causing fake "password authentication failed" errors
  - Switched from asyncpg to psycopg3 driver for better connection handling
  - Simplified database session management to match proven patterns
  - Added explicit commit/rollback logic to session management
  - Fixed database commits that were missing after simplification
- **Middleware**: Fixed ClientDisconnect exceptions during request streaming
  - Added proper exception handling for client disconnections
  - Returns 499 status code when clients disconnect mid-request
- **Dependencies**: Fixed missing OpenTelemetry exporter package causing startup crashes
- **Deprecations**: Updated Pydantic v2 deprecated `schema_extra` to `json_schema_extra`
- **Dashboard**: Fixed template errors when performance data is empty

### Changed
- Database driver changed from `postgresql+asyncpg` to `postgresql+psycopg` 
- Simplified database session handling for better reliability
- Updated all documentation to reflect actual implementation status

### Security
- All authentication and security features remain intact
- HMAC signature validation continues to work as expected

## [2025.7.1] - 2025-07-01

### Added
- Initial production release of Analytics Event Collector API
- FastAPI-based REST API with async support
- PostgreSQL database with SQLAlchemy ORM
- HMAC-SHA256 authentication with timestamp validation
- API key authentication as fallback
- Request compression support (zlib/deflate)
- Batch event processing with bulk insert optimization
- Redis-based distributed rate limiting with in-memory fallback
- Comprehensive analytics dashboard with 10+ views
- Prometheus metrics endpoint
- OpenTelemetry tracing support
- Structured JSON logging with correlation IDs
- Docker multi-stage builds with health checks
- Database connection pooling with circuit breakers
- Request size limiting middleware
- Comprehensive test suite with pytest

### Dashboard Features
- Apps overview with statistics
- Events timeline visualization
- Event details and metadata explorer
- Search and filtering capabilities
- Device analytics (models, OS versions)
- Feature usage tracking
- Error analytics
- Performance metrics
- User journey visualization
- JSON metadata analyzer

### Production Features
- Connection pool monitoring
- Circuit breaker pattern for failures
- Graceful degradation
- Health check endpoint with component status
- Non-root Docker user
- Environment-based configuration
- Database migration support with Alembic

## Known Issues

### Bugs
1. **Stale Connection Errors** (Fixed in 2025.7.5)
   - PostgreSQL connections would become stale and show as "password authentication failed"
   - Required switching to psycopg3 driver and simplifying session management

2. **ClientDisconnect Exceptions** (Fixed in 2025.7.5)
   - Middleware would crash when clients disconnected during requests
   - Now handled gracefully with 499 status code

### Not Implemented
1. **CI/CD Pipeline**
   - No GitHub Actions or automated deployment setup
   - Manual deployment required

2. **Async Background Processing**
   - Configuration flag `ENABLE_ASYNC_PROCESSING` exists but no queue implementation
   - All processing is synchronous

3. **Response Compression**
   - Only request decompression is implemented
   - No response compression middleware

4. **Data Retention**
   - No automatic cleanup of old events
   - No archival process implemented

5. **Application-Level Caching**
   - Redis is only used for rate limiting
   - No caching of analytics queries

6. **Performance Optimizations**
   - `UVLOOP_ENABLED` flag exists but uvloop not actually used
   - `USE_ORJSON` flag exists but orjson not actually used

7. **Load Testing**
   - No verified capacity for millions of requests/day claim
   - No load testing framework or results