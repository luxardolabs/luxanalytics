# ---- app: the shared base — runtime deps from the lock + the application ----
FROM python:3.14-slim AS app

WORKDIR /app

# System dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Poetry
# Matches the Poetry that writes poetry.lock (2.x reads the [project] table).
ENV POETRY_VERSION=2.4.1
ENV POETRY_HOME=/opt/poetry
ENV POETRY_VIRTUALENVS_CREATE=false
ENV POETRY_NO_INTERACTION=1

RUN pip install --no-cache-dir poetry==${POETRY_VERSION}

# Install dependencies (cached layer)
COPY pyproject.toml poetry.lock* VERSION ./
RUN poetry install --no-root --only main

# Copy application
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./

# Non-root user (switched to in the production stage)
RUN useradd -m -u 1000 luxanalytics && chown -R luxanalytics:luxanalytics /app

# ---- test: the shipped app layers + the dev group, from the SAME lock ----
# Built LOCALLY by `make test` (bare luxanalytics:test, never pushed). Source is over-mounted at
# run time, so this rebuilds only when the lock changes (FLEET-BUILD-DEPLOY-STANDARD, "Lint & test
# images"). Runs as root so the over-mounted checkout's ownership doesn't matter.
FROM app AS test
RUN poetry install --no-root --with dev

# ---- production: what ships (the default — last — stage) ----
FROM app AS production
USER luxanalytics

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:4000/health || exit 1

EXPOSE 4000

# Provenance: the canonical build-arg names feed the portable OCI labels
# (luxarch --doc FLEET-BUILD-DEPLOY-STANDARD, "Image labels"). .created is RFC-3339 UTC.
ARG BUILD_VERSION= BUILD_COMMIT= BUILD_TIMESTAMP=
ENV BUILD_TIMESTAMP=${BUILD_TIMESTAMP}
ENV BUILD_COMMIT=${BUILD_COMMIT}

LABEL org.opencontainers.image.version="$BUILD_VERSION" \
      org.opencontainers.image.revision="$BUILD_COMMIT" \
      org.opencontainers.image.created="$BUILD_TIMESTAMP" \
      org.opencontainers.image.source="https://github.com/luxardolabs/luxanalytics" \
      org.opencontainers.image.title="luxanalytics"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "4000"]
