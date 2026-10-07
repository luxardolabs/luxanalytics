FROM python:3.14-slim

WORKDIR /app

# System dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Poetry
ENV POETRY_VERSION=1.7.1
ENV POETRY_HOME=/opt/poetry
ENV POETRY_VIRTUALENVS_CREATE=false
ENV POETRY_NO_INTERACTION=1

RUN pip install --no-cache-dir poetry==${POETRY_VERSION}

# Install dependencies (cached layer)
COPY pyproject.toml poetry.lock* ./
RUN poetry install --no-root --only main

# Copy application
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./

# Non-root user
RUN useradd -m -u 1000 luxanalytics && chown -R luxanalytics:luxanalytics /app
USER luxanalytics

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:4000/health || exit 1

EXPOSE 4000

# Provenance: the canonical build-arg names feed the portable OCI labels
# (luxarch --doc FLEET-BUILD-DEPLOY-STANDARD, "Image labels"). .created is RFC-3339 UTC.
ARG BUILD_VERSION= BUILD_COMMIT= BUILD_TIMESTAMP=
ENV APP_VERSION=${BUILD_VERSION}
ENV BUILD_TIMESTAMP=${BUILD_TIMESTAMP}
ENV BUILD_COMMIT=${BUILD_COMMIT}

LABEL org.opencontainers.image.version="$BUILD_VERSION" \
      org.opencontainers.image.revision="$BUILD_COMMIT" \
      org.opencontainers.image.created="$BUILD_TIMESTAMP" \
      org.opencontainers.image.source="https://github.com/luxardolabs/luxanalytics" \
      org.opencontainers.image.title="luxanalytics"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "4000"]
