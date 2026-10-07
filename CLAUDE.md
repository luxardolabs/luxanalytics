# CLAUDE.md

This file provides guidance to Claude Code when working with this repository.

## Project Overview

**LuxAnalytics** — Production analytics event collector API for iOS apps. FastAPI + PostgreSQL + Redis, with a Tailwind/HTMX dashboard. Runs on OVH at `analytics.luxardolabs.com`.

## Architecture

```
Router → View Service (DashboardService) → Core Service (AnalyticsService) → CRUD → DB
```

- **Routers** call view services only — never touch CRUD directly
- **View services** are thin facades that delegate to core services
- **Core services** contain business logic, call CRUD methods
- **CRUD** files are the ONLY place raw SQL or SQLAlchemy queries exist
- **No inline JS** in templates — all JS lives in `static/js/`, HTMX-first
- **Separate routes** for full pages vs HTMX partials — never sniff `HX-Request` headers
- **Slider panels** for detail views — zero modals, zero browser dialogs
- **Jinja2 macros** for all UI components — use them everywhere

## File Naming Convention

All files MUST use these suffixes:
- `_model.py` — SQLAlchemy models
- `_schema.py` — Pydantic schemas
- `_service.py` — Business logic services
- `_view_service.py` — Template-facing view services (if separate from core)
- `_crud.py` — Database queries
- `_router.py` — FastAPI route handlers

## Directory Structure

```
app/
├── api/v1/routers/          # API endpoints (HMAC/DSN auth)
│   ├── events_router.py     # Event ingestion
│   └── stats_router.py      # Stats JSON API
├── web/routers/             # Dashboard web routes
│   ├── dashboard_router.py  # All dashboard pages + HTMX partials
│   ├── apps_router.py       # App management with slider panels
│   └── auth_router.py       # Login/logout
├── web/
│   ├── templates.py         # Shared Jinja2 templates instance
│   ├── template_context.py  # Global context injection
│   └── template_filters.py  # Custom Jinja2 filters (from LuxWX)
├── core/
│   ├── config.py            # Pydantic settings
│   ├── auth.py              # Session-based dashboard auth
│   ├── security.py          # HMAC authentication
│   ├── dsn_auth.py          # DSN-based authentication
│   ├── redis_client.py      # Redis connection
│   ├── rate_limiter.py      # Rate limiting
│   ├── logging.py           # Structured JSON logging
│   ├── telemetry.py         # OpenTelemetry + Prometheus
│   ├── constants.py         # App constants
│   └── middleware/
│       ├── request_middleware.py   # Logging, rate limit middleware
│       └── security_headers.py    # CSP, HSTS, X-Frame-Options
├── db/
│   ├── database.py          # Async SQLAlchemy engine + sessions
│   └── pool_monitor.py      # Connection pool monitoring
├── models/
│   ├── base.py              # UUIDMixin, TimestampMixin, SoftDeleteMixin
│   ├── event_model.py       # Event with promoted columns + properties JSONB
│   ├── device_model.py      # Device table (upserted on ingest)
│   └── app_model.py         # App registration (public_id, project_id)
├── schemas/
│   ├── event_schema.py      # Event Pydantic schemas
│   └── app_schema.py        # App Pydantic schemas
├── crud/
│   ├── event_crud.py        # ALL event queries (raw SQL for analytics)
│   ├── device_crud.py       # Device queries with time filtering
│   ├── app_crud.py          # App CRUD
│   └── analytics_crud.py    # Shared analytics query helpers
├── services/
│   ├── analytics_service.py # Core business logic (~500 lines)
│   ├── event_service.py     # Ingest: splits metadata → promoted cols + properties
│   ├── app_service.py       # App management
│   └── dashboard/
│       └── service.py       # Thin facade for dashboard routes
├── templates/
│   ├── layouts/base.html    # Unified layout (Tailwind, HTMX, Alpine, Chart.js, ECharts)
│   ├── components/nav.html  # Top nav with HTMX app dropdown
│   ├── macros/              # LuxWX macro library (12 files, ~5000 lines)
│   ├── pages/dashboard/     # Full page templates (9 pages)
│   └── partials/dashboard/  # HTMX content partials + slider panels
├── static/
│   ├── css/compiled.css     # Tailwind compiled output
│   ├── js/charts.js         # Chart.js + ECharts auto-initializer
│   └── vendor/              # Alpine, HTMX, Chart.js, ECharts
└── main.py                  # FastAPI app factory
```

## Key Commands (Makefile)

### Development
```bash
make dev              # Start with logs (compose up)
make up               # Start detached
make down             # Stop all
make restart          # Restart
make logs             # Tail all logs
make logs-app         # Tail app logs only
make shell            # Bash into app container
make shell-db         # psql into database
make test             # Run pytest in container
make test-local       # Run pytest locally (cd src)
make migrate          # Run Alembic migrations
make migrate-create   # Create new migration (interactive)
make css              # Build Tailwind CSS
make css-watch        # Watch mode for Tailwind
make status           # Health check + container status
make backup           # Backup local database
```

### Build & Registry
```bash
make external-build         # Build + push to registry.example
make external-build-latest  # Same + :latest tag
make local-build            # Build + push to local registry
make release                # Push to both registries
make version                # Show current version info
```

### Production (OVH via jump host)
```bash
make prod-deploy      # Pull + restart on production
make prod-push        # Push compose/env config to prod server
make prod-release     # external-build + prod-push + prod-deploy (full release)
make prod-restart     # Restart app container only
make prod-stop        # Stop all production containers
make prod-logs        # Tail production logs
make prod-status      # Container status on prod
make prod-shell       # Bash into prod app container
make prod-shell-db    # psql into prod database
make prod-migrate     # Run migrations on prod
make prod-backup      # Backup prod DB → local backups/
make prod-nginx       # Push nginx config + reload
make prod-version     # Show running image version
```

### Typical deploy workflow
```bash
make external-build && make prod-deploy
# or all-in-one:
make prod-release
```

## Technology Stack

- **Python 3.14** with Poetry
- **FastAPI** + **uvicorn** (port 4000)
- **PostgreSQL 16** (async via psycopg3 + SQLAlchemy 2.0)
- **Redis 7** (rate limiting, sessions)
- **Alembic** (migrations)
- **Tailwind CSS** (compiled, not CDN) with orange brand palette (#ffa500)
- **HTMX** + **Alpine.js** (toasts only) — zero inline JS
- **Chart.js** (timelines) + **ECharts** (Sankey, heatmaps)
- **Jinja2** macros from LuxWX

## Brand & UI

- **Orange primary**: `#ffa500` with warm dark surfaces (`#121212`)
- Colors from colorffy flat palette — all custom in `tailwind.config.js`
- Slider panels for all detail views, never modals
- `info_box` macro for notices (from `macros/badges.html`)
- `pagination_bar` macro for tables (from `macros/tables.html`)
- `time_pills` macro with Alpine.js client-side selection state

## Authentication

- **Dashboard**: Session-based (cookie), configured via `DASHBOARD_USERNAME` / `DASHBOARD_PASSWORD`
- **API ingest**: HMAC-SHA256 (primary), DSN/Basic auth (Sentry-style), API key (fallback)
- API auth is unaffected by dashboard auth

## Key Technical Notes

- **Event ingest** splits incoming metadata into promoted columns (`device_model`, `os_version`, `app_version`, `platform`, `device_id`) + `properties` JSONB, and upserts the device table
- **Raw SQL queries** in `event_crud.py` use `CAST(:app_id AS VARCHAR) IS NULL OR app_id = :app_id` pattern to handle NULL app_id filtering in PostgreSQL
- **Journey page** requires app selection — cross-app Sankey flows are meaningless
- **Device queries** filter on `Device.last_seen` for time range
- **LoggingMiddleware** has early return for non-API routes to prevent body stream consumption on Python 3.14
- **RequestSizeLimitMiddleware** is disabled — nginx handles body size limits
- **`EVENT_TIMESTAMP_FUTURE_TOLERANCE`** defaults to 60 seconds

## Production Infrastructure

- **Host**: OVH prod-node (`prod-node.example` via jump host `jump.example`)
- **Path**: `/opt/luxardolabs/luxanalytics`
- **Registry**: `registry.example/luxardolabs/luxanalytics`
- **Nginx**: Reverse proxy config at `deploy/prod/analytics.luxardolabs.com.conf`
- **Compose**: `deploy/prod/compose.yaml` with `.env.prod`
- **Port mapping**: 4000:4000 (no mental remapping)

## Environment Variables

See `deploy/prod/.env.prod` for production values. Key ones:
- `DATABASE_URL` — must use `postgresql+psycopg://` driver
- `DASHBOARD_PASSWORD` — change from default
- `HMAC_KEYS` / `API_KEYS` — JSON strings
- `EXTERNAL_URL` — `https://analytics.luxardolabs.com`
