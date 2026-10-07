.PHONY: help build up down restart logs shell test migrate clean dev prod css css-watch \
       local-build local-build-latest external-build external-build-latest release release-latest \
       buildx-setup \
       prod-push prod-deploy prod-restart prod-logs prod-status prod-shell prod-nginx prod-release

# =============================================================================
# Configuration
# =============================================================================
APP_NAME := luxanalytics
LOCAL_REGISTRY := docker.registry.example:5000
EXTERNAL_REGISTRY := registry.example
BUILDER_NAME := luxanalytics-builder

# Image name
IMAGE_NAME := luxanalytics
LOCAL_IMAGE := $(LOCAL_REGISTRY)/luxardolabs/$(IMAGE_NAME)
EXTERNAL_IMAGE := $(EXTERNAL_REGISTRY)/luxardolabs/$(IMAGE_NAME)

# Version from directory path: /luxanalytics/1.0/3 -> 1.0.3
PWD := $(shell pwd)
VERSION_MAJOR_MINOR := $(shell basename $(shell dirname $(PWD)))
VERSION_PATCH := $(shell basename $(PWD))
BUILD_VERSION := $(VERSION_MAJOR_MINOR).$(VERSION_PATCH)
BUILD_COMMIT := $(shell git rev-parse --short HEAD 2>/dev/null || echo "unknown")
BUILD_TIMESTAMP := $(shell date -u +"%Y-%m-%dT%H:%M:%SZ")

# Build args
BUILD_ARGS := --build-arg VERSION=$(BUILD_VERSION) \
              --build-arg BUILD_TIMESTAMP=$(BUILD_TIMESTAMP)

# Cache control: use NOCACHE=1 to disable
ifdef NOCACHE
  CACHE_FLAG := --no-cache
else
  CACHE_FLAG :=
endif

# =============================================================================
# Help
# =============================================================================
help:
	@echo "LuxAnalytics v$(BUILD_VERSION) — Analytics Event Collector"
	@echo ""
	@echo "Development:"
	@echo "  make build       - Build Docker images"
	@echo "  make up          - Start all services (detached)"
	@echo "  make dev         - Start all services (with logs)"
	@echo "  make down        - Stop all services"
	@echo "  make restart     - Restart all services"
	@echo "  make logs        - View logs (all services)"
	@echo "  make logs-app    - View application logs"
	@echo "  make shell       - Open shell in app container"
	@echo "  make shell-db    - Open psql in database"
	@echo "  make test        - Run test suite"
	@echo "  make migrate     - Run database migrations"
	@echo "  make status      - Quick health check"
	@echo "  make css         - Build Tailwind CSS"
	@echo "  make css-watch   - Watch and rebuild CSS on changes"
	@echo ""
	@echo "Local Registry ($(LOCAL_REGISTRY)):"
	@echo "  make local-build         - Build and push to local registry"
	@echo "  make local-build-latest  - Build and push with :latest tag"
	@echo ""
	@echo "External Registry ($(EXTERNAL_REGISTRY)):"
	@echo "  make external-build         - Build and push to external registry"
	@echo "  make external-build-latest  - Build and push with :latest tag"
	@echo ""
	@echo "Release:"
	@echo "  make release         - Build and push to both registries"
	@echo "  make release-latest  - Build and push to both with :latest tag"
	@echo ""

# =============================================================================
# Buildx Setup
# =============================================================================
buildx-setup:
	@docker buildx inspect $(BUILDER_NAME) > /dev/null 2>&1 || \
		docker buildx create --name $(BUILDER_NAME) --use --driver docker-container
	@docker buildx use $(BUILDER_NAME)

# =============================================================================
# Local Registry
# =============================================================================
local-build: buildx-setup css
	@echo "Building and pushing to local registry..."
	@echo "Image: $(LOCAL_IMAGE):$(BUILD_VERSION)"
	docker buildx build --platform linux/amd64 \
		$(BUILD_ARGS) \
		-t $(LOCAL_IMAGE):$(BUILD_VERSION) \
		$(CACHE_FLAG) --push .
	@echo "✅ Pushed $(LOCAL_IMAGE):$(BUILD_VERSION)"

local-build-latest: local-build
	@echo "Tagging as latest..."
	docker buildx build --platform linux/amd64 \
		$(BUILD_ARGS) \
		-t $(LOCAL_IMAGE):latest \
		--push .
	@echo "✅ Pushed $(LOCAL_IMAGE):latest"

# =============================================================================
# External Registry
# =============================================================================
external-build: buildx-setup css
	@echo "Building and pushing to external registry..."
	@echo "Image: $(EXTERNAL_IMAGE):$(BUILD_VERSION)"
	@echo "REDACTED-REGISTRY-CREDENTIAL" | docker login $(EXTERNAL_REGISTRY) -u luxardolabs --password-stdin
	docker buildx build --platform linux/amd64 \
		$(BUILD_ARGS) \
		-t $(EXTERNAL_IMAGE):$(BUILD_VERSION) \
		$(CACHE_FLAG) --push .
	@docker logout $(EXTERNAL_REGISTRY)
	@echo "✅ Pushed $(EXTERNAL_IMAGE):$(BUILD_VERSION)"

external-build-latest: buildx-setup css
	@echo "Building and pushing to external registry with :latest..."
	@echo "REDACTED-REGISTRY-CREDENTIAL" | docker login $(EXTERNAL_REGISTRY) -u luxardolabs --password-stdin
	docker buildx build --platform linux/amd64 \
		$(BUILD_ARGS) \
		-t $(EXTERNAL_IMAGE):$(BUILD_VERSION) \
		-t $(EXTERNAL_IMAGE):latest \
		$(CACHE_FLAG) --push .
	@docker logout $(EXTERNAL_REGISTRY)
	@echo "✅ Pushed $(EXTERNAL_IMAGE):$(BUILD_VERSION) + latest"
	docker buildx build --platform linux/amd64 \
		$(BUILD_ARGS) \
		-t $(EXTERNAL_IMAGE):latest \
		--push .
	@echo "✅ Pushed $(EXTERNAL_IMAGE):latest"

# =============================================================================
# Release (Both Registries)
# =============================================================================
release: local-build external-build
	@echo "✅ Released $(BUILD_VERSION) to both registries"

release-latest: local-build-latest external-build-latest
	@echo "✅ Released $(BUILD_VERSION) + latest to both registries"

# =============================================================================
# Version Info
# =============================================================================
version:
	@echo "Version: $(BUILD_VERSION)"
	@echo "Commit: $(BUILD_COMMIT)"
	@echo "Local:  $(LOCAL_IMAGE):$(BUILD_VERSION)"
	@echo "External: $(EXTERNAL_IMAGE):$(BUILD_VERSION)"

images:
	@echo "Local registry:"
	@docker images $(LOCAL_IMAGE) --format "table {{.Tag}}\t{{.CreatedAt}}\t{{.Size}}" 2>/dev/null | head -5
	@echo ""
	@echo "External registry:"
	@docker images $(EXTERNAL_IMAGE) --format "table {{.Tag}}\t{{.CreatedAt}}\t{{.Size}}" 2>/dev/null | head -5

# =============================================================================
# Development
# =============================================================================
build:
	docker compose build --no-cache

build-fast:
	docker compose build

up:
	docker compose up -d
	@echo "✅ LuxAnalytics is running at https://localhost:4000"

dev:
	docker compose up

down:
	docker compose down

restart:
	docker compose restart

logs:
	docker compose logs -f

logs-app:
	docker compose logs -f luxanalytics_app

logs-db:
	docker compose logs -f luxanalytics_db

shell:
	docker compose exec luxanalytics_app /bin/bash

shell-db:
	docker compose exec luxanalytics_db psql -U luxanalytics -d luxanalytics

test:
	docker compose exec luxanalytics_app pytest tests/ -v

test-local:
	cd src && pytest tests/ -v

migrate:
	docker compose exec luxanalytics_app alembic upgrade head

migrate-down:
	docker compose exec luxanalytics_app alembic downgrade -1

migrate-create:
	@read -p "Enter migration message: " msg; \
	docker compose exec luxanalytics_app alembic revision --autogenerate -m "$$msg"

clean:
	docker compose down -v
	rm -rf __pycache__ .pytest_cache
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true

status:
	@echo "🔍 LuxAnalytics v$(BUILD_VERSION)"
	@docker compose ps
	@echo ""
	@curl -sk https://localhost:4000/health 2>/dev/null | python3 -m json.tool 2>/dev/null || echo "❌ API not responding"

health:
	@curl -sk https://localhost:4000/health | python3 -m json.tool || echo "❌ Service not responding"

ps:
	docker compose ps

backup:
	@mkdir -p backups
	@docker compose exec -T luxanalytics_db pg_dump -U luxanalytics luxanalytics > backups/luxanalytics_$$(date +%Y%m%d_%H%M%S).sql
	@echo "✅ Database backed up to backups/"

restore:
	@if [ -z "$(FILE)" ]; then echo "Usage: make restore FILE=backups/luxanalytics_YYYYMMDD_HHMMSS.sql"; exit 1; fi
	@docker compose exec -T luxanalytics_db psql -U luxanalytics -d luxanalytics < $(FILE)
	@echo "✅ Database restored from $(FILE)"

work:
	make build-fast
	make up
	make migrate
	make logs

quick:
	docker compose restart luxanalytics_app
	docker compose logs -f luxanalytics_app

css:
	npm run build:css

css-watch:
	npm run dev:css

check-env:
	@command -v docker >/dev/null 2>&1 && echo "✅ Docker" || echo "❌ Docker"
	@command -v docker compose >/dev/null 2>&1 && echo "✅ Compose" || echo "❌ Compose"
	@command -v python3 >/dev/null 2>&1 && echo "✅ Python" || echo "❌ Python"
	@command -v npm >/dev/null 2>&1 && echo "✅ npm" || echo "❌ npm"
	@test -f .env.dev && echo "✅ .env.dev" || echo "❌ .env.dev"

# =============================================================================
# Production Deployment (OVH prod-node via jump host)
# =============================================================================
PROD_JUMP := jump.example
PROD_HOST := root@prod-node.example
PROD_PATH := /opt/luxardolabs/luxanalytics
PROD_SSH := ssh $(PROD_JUMP) "ssh $(PROD_HOST)

prod-push:
	@echo "Pushing deploy config to production..."
	@tar -czf /tmp/luxanalytics-deploy-$(BUILD_VERSION).tar.gz -C deploy/prod .
	@scp /tmp/luxanalytics-deploy-$(BUILD_VERSION).tar.gz $(PROD_JUMP):/tmp/
	@ssh $(PROD_JUMP) "scp /tmp/luxanalytics-deploy-$(BUILD_VERSION).tar.gz $(PROD_HOST):/tmp/"
	@$(PROD_SSH) 'mkdir -p $(PROD_PATH) && tar -xzf /tmp/luxanalytics-deploy-$(BUILD_VERSION).tar.gz -C $(PROD_PATH)/'"
	@echo "✅ Deploy config pushed to $(PROD_PATH)"

prod-deploy:
	@echo "Deploying LuxAnalytics $(BUILD_VERSION) to production..."
	@$(PROD_SSH) 'echo REDACTED-REGISTRY-CREDENTIAL | docker login $(EXTERNAL_REGISTRY) -u luxardolabs --password-stdin && cd $(PROD_PATH) && docker compose --env-file .env.prod pull && docker compose --env-file .env.prod up -d && docker logout $(EXTERNAL_REGISTRY)'"
	@echo "✅ Deployed $(BUILD_VERSION)"

prod-restart:
	@echo "Restarting LuxAnalytics on production..."
	@$(PROD_SSH) 'cd $(PROD_PATH) && docker compose restart luxanalytics_app'"
	@echo "✅ Restarted"

prod-stop:
	@echo "Stopping LuxAnalytics on production..."
	@$(PROD_SSH) 'cd $(PROD_PATH) && docker compose down'"
	@echo "✅ Stopped"

prod-logs:
	@$(PROD_SSH) 'cd $(PROD_PATH) && docker compose logs --tail 50 luxanalytics_app'"

prod-status:
	@$(PROD_SSH) 'docker ps --filter name=luxanalytics --format \"table {{.Names}}\t{{.Image}}\t{{.Status}}\"'"

prod-shell:
	@ssh -t $(PROD_JUMP) "ssh -t $(PROD_HOST) 'docker exec -it luxanalytics_app /bin/bash'"

prod-shell-db:
	@ssh -t $(PROD_JUMP) "ssh -t $(PROD_HOST) 'docker exec -it luxanalytics_db psql -U luxanalytics -d luxanalytics'"

prod-nginx:
	@echo "Pushing nginx config to production..."
	@scp deploy/prod/analytics.luxardolabs.com.conf $(PROD_JUMP):/tmp/
	@ssh $(PROD_JUMP) "scp /tmp/analytics.luxardolabs.com.conf $(PROD_HOST):/opt/nginx/conf.d/"
	@$(PROD_SSH) 'docker exec nginx nginx -s reload'"
	@echo "✅ Nginx config updated and reloaded"

prod-migrate:
	@echo "Running migrations on production..."
	@$(PROD_SSH) 'docker exec luxanalytics_app alembic upgrade head'"
	@echo "✅ Migrations complete"

prod-backup:
	@echo "Backing up production database..."
	@$(PROD_SSH) 'docker exec luxanalytics_db pg_dump -U luxanalytics luxanalytics | gzip > /tmp/luxanalytics-backup-$$(date +%Y%m%d).sql.gz'"
	@ssh $(PROD_JUMP) "scp $(PROD_HOST):/tmp/luxanalytics-backup-*.sql.gz /tmp/"
	@scp $(PROD_JUMP):/tmp/luxanalytics-backup-*.sql.gz backups/ 2>/dev/null || mkdir -p backups && scp $(PROD_JUMP):/tmp/luxanalytics-backup-*.sql.gz backups/
	@echo "✅ Backup saved to backups/"

prod-version:
	@$(PROD_SSH) 'docker inspect luxanalytics_app --format \"{{.Config.Image}}\" 2>/dev/null || echo not running'"

prod-release: external-build prod-push prod-deploy
	@echo "✅ Released $(BUILD_VERSION) to production"
