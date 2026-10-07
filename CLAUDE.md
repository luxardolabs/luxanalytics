# CLAUDE.md

This file provides guidance to Claude Code when working with this repository.

<!-- luxarch:claude-pointer asset v6 - DO NOT edit this marker line; it is how repo.claude_pointer_present knows your copy is current. Re-emit with `luxarch --emit claude-pointer`. -->

## How to work here (fleet conduct — read the standard, not just this block)

**`luxarch --doc FLEET-AGENT-CONDUCT-STANDARD` — read it in full before your first change.** It is the one home for *how* agents work in this fleet. This block is a pointer plus the handful of rules that get broken most; it is not a summary and does not replace reading it.

**Run `/fleet-start` at the start of every session and after every compact.** It rehydrates from LuxPM and restates the session rules.

**Report the result, not the mountain.** No "heavy", "multi-hour", "the big one", no narrating difficulty. Done + next in one line, with numbers.

**Decide; do not hand back a menu.** Whether to ask the owner is decided by the **class of action**, never by how confident you feel:

- **Ask** — deleting anything; changing scope; a deferral/allowlist/exemption; publishing outward (pushing another repo, a force-push, a history rewrite); a genuine product fork where the choice is taste, not correctness.
- **Do it** — aligning code to a ratified standard or a guard red; anything you have evidence for that is reversible in one commit. The standard already decided; say what you did.

**Work the guard reds in `luxarch --plan` order. Never ask which family or sweep is next.** The order is decided. An escalation covers ONE site: its family keeps going.

**An owner hold is exactly as wide as the owner said.** "Hold off on X" excludes X and nothing else. It is not permission to pause, ask, or check in about anything outside X. Skip the held family, say so in one line, and keep burning down the rest.

**End every turn that worked guard reds with `reds: N (was M)`**, plus the held families by name. A turn that ends on a question while N > 0, outside an ask-class, is the failure this block exists to stop.

When you do ask: **one decision per message**, the evidence that makes it answerable, your recommendation stated as one, and a question answerable in one word. **A recommendation that ends in a menu is not a recommendation** — if you rejected the alternatives, re-offering them asks the owner to redo your analysis.

**Use the fleet skills; don't improvise the procedure.** `/fleet-start` to open a session. `/wrap-up` before you call anything done (tests, every red in touched files, docs, gate, LuxPM closed out, all with evidence). `/pin-bump` to upgrade the guards. `/escalate` when a guard is wrong. `/release` to cut a release.

**You touched it, you own it.** Edit a file for any reason and it has a mypy, ruff or luxarch red: fix every one in that file, not just yours. Never spend time proving a red predates you; fix it. Test what you changed first. **Before fixing any mypy red, read `luxlint --playbook mypy-sweep` in full.**

**Align or escalate; never route around.** A guard red is fixed by changing the code, or escalated to the guard maintainer as genuinely wrong. Never by an exemption, a `# noqa`, a deferral, or a local config. Verification is not authorization: proving something is unreferenced does not license deleting it.

**Escalations go in THIS repo's LuxPM project** — label `fleet-escalation`, title `[<guard> ESCALATION] …`, self-contained enough to forward whole. **Search LuxPM for an existing issue first** (and comment on it if found); filing a new one is pre-authorized. **Never a GitHub issue** — there is no fallback. The maintainer sweeps the label across every project and picks it up where you filed it.

**A red stays RED while its escalation is open.** The fleet does not gate CI on red. A lit red is honest; a silenced one is a lie you will inherit.

**Commit as `luxardolabs`** using the global git config, and never `git -c user.email=…`. No AI attribution in commit messages.

## Project Overview

**LuxAnalytics** — Production analytics event collector API for iOS apps. FastAPI + PostgreSQL + Redis, with a Tailwind/HTMX dashboard. Runs on OVH at `analytics.luxardolabs.com`.

## Architecture

```
Router → View Service (DashboardViewService) → Core Service (AnalyticsCoreService) → CRUD → DB
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
│   ├── logging_config.py    # Fleet stdlib JSON logging (luxarch --emit logging)
│   ├── telemetry.py         # OpenTelemetry + Prometheus
│   ├── constants.py         # App constants
│   └── middleware/
│       ├── request_middleware.py   # Logging, rate limit middleware
│       └── security_headers.py    # CSP, HSTS, X-Frame-Options
├── db/
│   ├── database.py          # Async SQLAlchemy engine + sessions
│   └── pool_monitor.py      # Connection pool monitoring
├── models/
│   ├── base_model.py        # Base (canonical naming_convention), UUIDMixin, TimestampMixin, SoftDeleteMixin
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
│   ├── core/                          # Business logic, transport-agnostic (*_core_service.py)
│   │   ├── analytics_core_service.py  # AnalyticsCoreService
│   │   ├── event_core_service.py      # EventCoreService: ingest splits metadata → promoted cols + properties
│   │   └── app_core_service.py        # AppCoreService: app management
│   └── views/                         # The web seam: shapes template context (*_view_service.py)
│       └── dashboard_view_service.py  # DashboardViewService
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
make test             # Full suite against a throwaway Postgres (test-db-up/down), canonical pytest config
make db-verify        # Migrate an EMPTY DB to head and diff it against the models
make migrate          # Run Alembic migrations
make migrate-create   # Create new migration (interactive)
make css              # Build Tailwind CSS
make css-watch        # Watch mode for Tailwind
make stack-status     # Health check + container status
make backup           # Backup local database
```

### Fleet guards (luxarch · luxlint · luxaudit)

```bash
make check            # THE gate: pins → honest → lint → mypy → test → arch → audit → gitleaks
make onboard-check    # wiring + honesty (NOT green)
make plan             # every arch red, phase-ordered
make status           # regenerate the committed .lux*-status.json files (commit them)
make arch-rule RULE=… / arch-file FILE=… / lint-file FILE=… / mypy-file FILE=…   # scoped re-runs
make format           # the canonical fixer (luxlint --format) — never a bare formatter
make guard-upgrade    # bump every guard pin to latest
```

- Copy `Makefile.local.example` → `Makefile.local` (gitignored): registry hosts, prod node, registry credential.
- Check the guard pins against latest at session start (`make guard-version-check`) and bump if behind.
- Reds stay red: align the code or escalate a wrong guard (`fleet-escalation` issue in LuxPM). Never defer to green.
- Read guard docs from the image: `luxarch --docs`, `--doc <NAME>`, `--playbook <slug>`.

### Build & Registry (emitted image block — `luxarch --emit image-block`)

```bash
make publish-sha      # Build + scan + push this commit as :sha-<commit> (moves the :dev alias)
make release          # Cut VERSION: build + scan + push :$(VERSION), then the GitHub Release (refuses a released VERSION, a dirty tree, an untagged HEAD)
make version          # Show the version and image refs
```

One registry (`$(REGISTRY)` in `Makefile.local`) holds the app images and the guards. Deploy tags are immutable: `:$(VERSION)` for prod, `:sha-<commit>` for anything else; `:dev` is an alias nothing pins.

### Production (OVH via jump host)

```bash
make prod-deploy      # prod-pin + prod-sync + pull + restart on production
make prod-sync        # Sync deploy/prod (compose + .env.prod + nginx conf) to the prod node
make prod-pin         # Pin TAG in .env.prod to a released version (PROD_TAG=… to roll back)
make prod-release     # release + prod-deploy (full release)
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
make release && make prod-deploy
# or all-in-one:
make prod-release
```

## Technology Stack

- **Python 3.14** with Poetry
- **FastAPI** + **uvicorn** (port 4000)
- **PostgreSQL 16** (async via asyncpg + SQLAlchemy 2.1 — the fleet driver; `pool_pre_ping` stays on, it is what catches stale pooled connections)
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

- **Host**: the prod node behind an ssh jump host — `PROD_HOST` / `PROD_JUMP` in `Makefile.local`
- **Path**: `/opt/luxardolabs/luxanalytics`
- **Registry**: `$(REGISTRY)/luxardolabs/luxanalytics` (host in `Makefile.local`); prod runs `${REGISTRY}/luxardolabs/luxanalytics:${TAG}` with both set in `deploy/prod/.env.prod`
- **Nginx**: Reverse proxy config at `deploy/prod/analytics.luxardolabs.com.conf`
- **Compose**: `deploy/prod/compose.yaml` with `.env.prod`
- **Port mapping**: 4000:4000 (no mental remapping)

## Environment Variables

See `deploy/prod/.env.prod` for production values. Key ones:

- `DATABASE_URL` — `postgresql+asyncpg://` (the fleet driver; the emitted test harness and `make db-verify` assume it)
- `DASHBOARD_PASSWORD` — change from default
- `HMAC_KEYS` / `API_KEYS` — JSON strings
- `EXTERNAL_URL` — `https://analytics.luxardolabs.com`
