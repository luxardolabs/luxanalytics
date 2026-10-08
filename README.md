# LuxAnalytics

The analytics event collector for Luxardo Labs' iOS apps: a FastAPI API that ingests events from the Swift SDK, and a Tailwind + HTMX dashboard to explore them. PostgreSQL stores the events, Redis holds the rate-limit windows.

The client is the Swift SDK, [luxardolabs/luxanalytics-swift](https://github.com/luxardolabs/luxanalytics-swift) (MIT). This repository is the server, which you can self-host (see Quick start and Production).

## Features

- **Ingest:** single or batched events (bulk insert, up to 1000 per request), zlib/deflate request compression, two authentication modes: HMAC-SHA256 signatures with replay protection, and Sentry-style DSNs.
- **Event model:** device context is promoted to columns (`device_model`, `os_version`, `app_version`, `platform`, `device_id`); everything else is `properties` JSONB. A `devices` table is upserted on ingest.
- **Dashboard:** overview, events, devices, errors, performance, features, journeys (Sankey and transition heatmap), feedback, a properties explorer with per-key deep dives, user profiles, sessions, and app management with DSNs. Session login; slider panels for detail views.
- **Operations:** per-app and per-IP rate limits (Redis, with an in-memory fallback), JSON logs on stdout, OpenTelemetry traces (a span per service method), a Prometheus scrape, and a health check that names the running build.

## Quick start (development)

Everything runs in containers through `make`; `make help` lists the targets.

```bash
cp .env.example .env.dev            # then set DASHBOARD_PASSWORD, HMAC_KEYS, ...
cp Makefile.local.example Makefile.local   # registry host, prod node (gitignored)
make network                        # the shared docker network, once per host
make dev-deploy                     # build + push this commit, pin it in .env.dev, start, smoke
make migrate                        # alembic upgrade head
```

The app listens on `http://localhost:4000`; the dashboard is `/dashboard/overview`.

## Development

```bash
make test        # the full suite against a throwaway Postgres and Redis
make check       # THE gate: guard pins, lint, mypy, tests, luxarch, luxaudit, gitleaks
make db-verify   # migrate an empty database to head and diff it against the models
make migrate-create   # a new Alembic revision
make css-watch   # live Tailwind rebuilds while editing templates
make format      # the fleet formatter
```

Architecture, conventions and the full command list are in `CLAUDE.md`.

## API Endpoints

### Core Endpoints

- `GET /` - Redirects to the dashboard
- `GET /dashboard/overview` - Analytics dashboard (session login)
- `GET /api/v1/stats/overview` - Cross-app stats as JSON (dashboard session required)
- `GET /health` - Health check: database, Redis, pool counters, version and build commit
- `GET /metrics` - Prometheus scrape (process, GC and `db_pool_*` series)

`/health` and `/metrics` are internal: the production nginx answers 404 for both, so scrape and probe them from inside the network.

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

curl -X POST "http://localhost:4000/api/v1/events/" \
  -H "Content-Type: application/json" \
  -H "X-HMAC-Signature: ${SIGNATURE}" \
  -H "X-Key-ID: ${KEY_ID}" \
  -H "X-Timestamp: ${TIMESTAMP}" \
  -d "${PAYLOAD}"
```

#### DSN-Style Endpoint (Sentry-compatible)

Each app registered in the dashboard (Apps) gets a DSN. `POST /api/v1/events/public` is the legacy DSN form, kept for older SDK builds.

```bash
# Using DSN format: https://PUBLIC_ID@host/api/v1/events/PROJECT_ID
DSN="https://abc123@analytics.example.com/api/v1/events/proj123"

# Extract components
PUBLIC_ID="abc123"
PROJECT_ID="proj123"

# Send event with Basic Auth
curl -X POST "http://localhost:4000/api/v1/events/${PROJECT_ID}" \
  -H "Content-Type: application/json" \
  -H "Authorization: Basic $(echo -n ${PUBLIC_ID}: | base64)" \
  -d '{"name": "test_event", "timestamp": "2024-01-01T12:00:00"}'
```

#### Compression Support

- `Content-Encoding: deflate` means the zlib format (RFC 1950) wrapping a DEFLATE stream, as RFC 9110 §8.4.1.2 defines it. The Swift SDK 1.1.0 and later sends exactly that.
- Raw DEFLATE (RFC 1951, no zlib wrapper) is still accepted, for apps on SDK 1.0.2 and earlier. New clients must not send it.
- Clients compress only above their threshold (the SDK's default is 1 KB).
- Where HMAC is used, the signature is over the compressed bytes.
- A body that is neither format gets `400 Invalid compressed data`.

#### Stats Endpoint

```bash
# Stats endpoint also requires HMAC authentication
curl -X GET "http://localhost:4000/api/v1/events/stats" \
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

A payload may be one event object, `{"events": [...]}` or a bare list. A batch is at most 1000 events and is bulk-inserted. A malformed payload is a 400, an invalid event a 422.

## Configuration

Environment variables, one file per environment (`.env.dev`, `deploy/prod/.env.prod`; the template is `.env.example`). The main ones:

| Variable                                                                                            | Purpose                                                                                      |
| --------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------- |
| `DATABASE_URL`                                                                                      | `postgresql+asyncpg://…`; keep `DB_POOL_PRE_PING=true` (it catches stale pooled connections) |
| `DB_POOL_SIZE`, `DB_POOL_MAX_OVERFLOW`, `DB_POOL_TIMEOUT`, `DB_POOL_RECYCLE`                        | Connection pool                                                                              |
| `REDIS_URL`                                                                                         | Rate-limit state shared across workers (in-memory fallback when unset or down)               |
| `HMAC_KEYS`                                                                                         | JSON `{"app_id": "secret"}` for signed ingest                                                |
| `RATE_LIMIT_REQUESTS`, `RATE_LIMIT_WINDOW`, `LOGIN_RATE_LIMIT`                                      | Request limits                                                                               |
| `TRUSTED_PROXIES`                                                                                   | Proxies whose `X-Forwarded-For` is believed                                                  |
| `ALLOWED_HOSTS`, `CORS_ORIGINS`, `EXTERNAL_URL`                                                     | Host and origin allowlists; the public base URL used in DSNs                                 |
| `EVENT_TIMESTAMP_FUTURE_TOLERANCE`                                                                  | Seconds an event may be in the future (default 60)                                           |
| `MAX_REQUEST_SIZE`                                                                                  | Request body limit (default 10 MB)                                                           |
| `DASHBOARD_USERNAME`, `DASHBOARD_PASSWORD`, `DASHBOARD_SESSION_SECRET`, `DASHBOARD_SESSION_TIMEOUT` | Dashboard login; the password is required                                                    |
| `OTEL_EXPORTER_OTLP_ENDPOINT`                                                                       | Turns tracing on; setup fails loudly if it is set and broken                                 |
| `LOG_LEVEL`                                                                                         | Log level (JSON lines; caller fields under `attributes`)                                     |

## Production

Production runs the released image (`make release` cuts `:VERSION`) from the one `compose.yml` with `deploy/prod/.env.prod`, behind nginx (`deploy/prod/analytics.luxardolabs.com.conf`).

```bash
make release && make prod-deploy   # or: make prod-release
make prod-migrate                  # migrations on the prod database
make prod-status / prod-logs / prod-version
```

## Troubleshooting

- **"Invalid compressed data":** send `Content-Encoding: deflate` and sign the compressed body.
- **HMAC failures:** the timestamp must be within the tolerance window; the signature is over `payload + timestamp`; the key id must be in `HMAC_KEYS`.
- **"password authentication failed" from the pool:** usually a stale pooled connection, not a password. Keep `DB_POOL_PRE_PING=true` and set `DB_POOL_RECYCLE` below the network idle timeout.
- **400 on every request after a deploy:** the proxy must forward `Host $host`; the app only answers hosts in `ALLOWED_HOSTS` (default: `EXTERNAL_URL`'s host and localhost).

## License

The server is licensed under the GNU Affero General Public License v3.0 (`LICENSE`).
