# ---- builder: resolve the lock into a venv; the build tooling never reaches the runtime ----
FROM python:3.14-slim AS builder

# Matches the Poetry that writes poetry.lock (2.x reads the [project] table).
ENV POETRY_VERSION=2.4.1 \
    POETRY_VIRTUALENVS_CREATE=false \
    POETRY_NO_INTERACTION=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH

RUN pip install --no-cache-dir poetry==${POETRY_VERSION} \
    && python -m venv /opt/venv

WORKDIR /build
COPY pyproject.toml poetry.lock VERSION ./
# Into the active venv (VIRTUAL_ENV), main group only; then pip leaves the venv: it vendors
# urllib3/msgpack/setuptools that no lock upgrade reaches, and the app never runs pip.
RUN poetry install --no-root --only main \
    && /opt/venv/bin/python -m pip uninstall -y pip

# ---- production: what ships (the default — last — stage) ----
FROM python:3.14-slim AS production

# Current OS packages (the release gate refuses fixable HIGH/CRITICAL), curl for the
# HEALTHCHECK only, and no pip in the system interpreter either.
RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip uninstall -y pip

ENV VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH

WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY VERSION ./
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./

RUN useradd -m -u 1000 luxanalytics && chown -R luxanalytics:luxanalytics /app
USER luxanalytics

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

# ---- test: the SHIPPED layers + the dev group, from the same lock ----
# FROM production, putting back what production stripped (FLEET-BUILD-DEPLOY-STANDARD, "Lint &
# test images"). Built LOCALLY by `make test` (bare luxanalytics:test, never pushed); source is
# over-mounted at run time. Root, so the over-mounted checkout's ownership doesn't matter.
FROM production AS test
USER root
ENV POETRY_VIRTUALENVS_CREATE=false POETRY_NO_INTERACTION=1
COPY pyproject.toml poetry.lock ./
RUN python -m ensurepip \
    && python -m pip install --no-cache-dir poetry==2.4.1 \
    && poetry install --no-root --with dev

# The default target stays the shipped image.
FROM production
