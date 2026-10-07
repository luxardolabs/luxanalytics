# =============================================================================
# LuxAnalytics — analytics event collector API (FastAPI + PostgreSQL + Redis)
# =============================================================================

# Private topology (registry hosts, prod node, registry credential) is kept OUT of this tree:
# set it in an untracked Makefile.local (see Makefile.local.example). Included FIRST so its
# values win over the empty defaults below.
-include Makefile.local

# The external-registry credential is a SECRET, not topology, so it lives in the gitignored
# .env.build (REGISTRY_USER / REGISTRY_PASSWORD), same as www / open-claim / luxof.life — never in
# a Makefile. `export` makes it visible to recipe shells, which read it as $$REGISTRY_PASSWORD.
# See luxarch --doc FLEET-BUILD-DEPLOY-STANDARD ("Versioning your ops config").
-include .env.build
export REGISTRY_USER REGISTRY_PASSWORD

# =============================================================================
# Configuration
# =============================================================================
APP_NAME := luxanalytics

# The fleet registry: holds this app's local images AND the three guard images.
REGISTRY ?=
# The external registry production pulls from (its credential is in .env.build, above).
EXTERNAL_REGISTRY ?=
REGISTRY_USER ?= luxardolabs

# Image name
IMAGE_NAME := luxanalytics
LOCAL_IMAGE := $(REGISTRY)/luxardolabs/$(IMAGE_NAME)
EXTERNAL_IMAGE := $(EXTERNAL_REGISTRY)/luxardolabs/$(IMAGE_NAME)

# The VERSION file is the one version source (CalVer YYYY.0M.MICRO for an app).
PWD := $(shell pwd)
VERSION := $(shell cat VERSION)
BUILD_VERSION := $(VERSION)
BUILD_COMMIT := $(shell git rev-parse --short HEAD 2>/dev/null || echo "unknown")
BUILD_TIMESTAMP := $(shell date -u +"%Y-%m-%dT%H:%M:%SZ")

# Build args
BUILD_ARGS := --build-arg BUILD_VERSION=$(BUILD_VERSION) \
              --build-arg BUILD_COMMIT=$(BUILD_COMMIT) \
              --build-arg BUILD_TIMESTAMP=$(BUILD_TIMESTAMP)

# Cache control: use NOCACHE=1 to disable
ifdef NOCACHE
  CACHE_FLAG := --no-cache
else
  CACHE_FLAG :=
endif

# =============================================================================
# Fleet guards — pinned (`:=`, a committed fact); see luxarch --doc FLEET-MAKEFILE-STANDARD
# =============================================================================
LUXARCH_VERSION  := 0.265.0
LUXLINT_VERSION  := 0.61.0
LUXAUDIT_VERSION := 0.13.0
LUXARCH  := $(REGISTRY)/luxardolabs/luxarch:$(LUXARCH_VERSION)
LUXLINT  := $(REGISTRY)/luxardolabs/luxlint:$(LUXLINT_VERSION)
LUXAUDIT := $(REGISTRY)/luxardolabs/luxaudit:$(LUXAUDIT_VERSION)
GUARD_RUN = docker run --rm -v $(PWD):/repo

# Host TLS cert dir for the local nginx (compose.yml); set in Makefile.local.
TLS_CERTS_DIR ?=
export TLS_CERTS_DIR

.DEFAULT_GOAL := help

.PHONY: help check guard-version-check guard-upgrade guard-registry honest lint mypy format test arch plan \
        arch-rule arch-file lint-file mypy-file status test-db-up test-db-down db-verify \
        audit gitleaks gitleaks-staged onboard-check \
        build build-fast up down restart logs logs-app logs-db shell shell-db test-local migrate migrate-down \
        migrate-create clean stack-status health ps backup restore work quick css css-watch check-env \
        local-build local-build-latest external-build external-build-latest release release-latest \
        buildx-setup version images \
        prod-push prod-deploy prod-restart prod-stop prod-logs prod-status prod-shell prod-shell-db \
        prod-nginx prod-migrate prod-backup prod-version prod-release

# THE fleet gate — byte-identical composition across every app repo. Make stops at the FIRST
# failing step; for every architecture red at once, run `make plan`.
check: guard-version-check honest lint mypy test arch audit gitleaks db-verify ## THE fleet gate — run before every commit

guard-registry:
	@[ -n "$(REGISTRY)" ] || { echo "REGISTRY unset — copy Makefile.local.example to Makefile.local"; exit 1; }

guard-version-check: guard-registry ## FATAL: fail if any guard pin is behind the published latest
	@rc=0; for g in luxarch luxlint luxaudit; do \
	  pin=$$(case $$g in luxarch) echo $(LUXARCH_VERSION);; luxlint) echo $(LUXLINT_VERSION);; luxaudit) echo $(LUXAUDIT_VERSION);; esac); \
	  docker pull -q $(REGISTRY)/luxardolabs/$$g:latest >/dev/null 2>&1 || true; \
	  latest=$$(docker run --rm $(REGISTRY)/luxardolabs/$$g:latest --version 2>/dev/null | awk '{print $$2}'); \
	  if [ -n "$$latest" ] && [ "$$latest" != "$$pin" ]; then \
	    printf '✗ %s pinned %s, latest %s — behind. Preview: --new-rules --since %s; then make guard-upgrade\n' "$$g" "$$pin" "$$latest" "$$pin"; rc=1; \
	  fi; done; exit $$rc

# luxarch:guard-upgrade asset v1 - DO NOT edit this marker line; it is how repo.emitted_assets_current knows your copy is current. Re-emit with `luxarch --emit guard-upgrade`.
guard-upgrade:  ## Bump every guard pin to the published latest (prints what newly bites)
	@for g in luxarch luxlint luxaudit; do \
	  docker pull -q $(REGISTRY)/luxardolabs/$$g:latest >/dev/null 2>&1 || true; \
	  latest=$$(docker run --rm $(REGISTRY)/luxardolabs/$$g:latest --version 2>/dev/null | awk '{print $$2}'); \
	  var=$$(echo $$g | tr a-z A-Z)_VERSION; \
	  old=$$(sed -n -E "s/^$$var[[:space:]]*:=[[:space:]]*//p" Makefile); \
	  if [ -z "$$old" ]; then echo "!! no $$var pin found in Makefile — NOT bumped"; continue; fi; \
	  if [ -z "$$latest" ]; then echo "!! could not read $$g:latest — $$var left at $$old"; continue; fi; \
	  checked=1; \
	  sed -i -E "s|^($$var[[:space:]]*:=[[:space:]]*).*|\\1$$latest|" Makefile; \
	  new=$$(sed -n -E "s/^$$var[[:space:]]*:=[[:space:]]*//p" Makefile); \
	  if [ "$$new" != "$$latest" ]; then echo "!! $$var did NOT change (still $$new)"; exit 1; fi; \
	  if [ "$$old" != "$$latest" ]; then echo "$$var $$old -> $$latest"; bumped=1; fi; \
	  [ "$$g" = luxarch ] && [ "$$old" != "$$latest" ] && docker run --rm -v $(PWD):/repo $(REGISTRY)/luxardolabs/luxarch:$$latest --new-rules --since $$old || true; \
	done; \
	if [ -n "$$bumped" ]; then echo "pins bumped — re-run make check"; \
	elif [ -n "$$checked" ]; then echo "all pins already at latest"; \
	else echo "!! could not reach the registry — NO pin was checked; currency NOT established"; exit 1; fi

honest: guard-registry ## HONESTY gate — a green `make check` must mean nothing was silently unchecked
	@$(GUARD_RUN) $(LUXARCH) --assert-scans
	@$(GUARD_RUN) $(LUXLINT) --preflight

lint: guard-registry ## ruff + format + eslint + secret checks (canonical config, mount-only)
	@$(GUARD_RUN) $(LUXLINT)

mypy: guard-registry ## mypy (fleet typed deps baked, mount-only)
	@$(GUARD_RUN) $(LUXLINT) --mypy

# THE canonical fixer (the only luxlint mode that writes). Never shell the formatter directly:
# with no local config it applies its default width and rewrites the tree wrong.
format: guard-registry ## Auto-fix + format Python and Markdown via luxlint (writes back)
	@docker run --rm --user $$(id -u):$$(id -g) -e HOME=/tmp -v $(PWD):/repo $(LUXLINT) --format

# ── The test harness: a THROWAWAY Postgres, never the dev database (FLEET-MAKEFILE-STANDARD) ──
# `make test` starts it, migrates it from the chain (the conftest does `alembic upgrade head`),
# runs the FULL suite under the canonical pytest config, and wipes it on every exit path. A skipped
# DB suite therefore cannot read as a pass: the database is always there.
TEST_IMAGE   := luxanalytics:test
TEST_DB      := luxanalytics_testdb
TEST_NET     := luxanalytics_testnet
TEST_PG      ?= postgres:16-alpine
TEST_DB_URL  := postgresql+asyncpg://test:test@$(TEST_DB):5432/test
# Settings the app requires to import. Values are throwaway; the suite only talks to TEST_DB.
TEST_ENV := -e TEST_DATABASE_URL=$(TEST_DB_URL) -e DATABASE_URL=$(TEST_DB_URL) \
            -e DATABASE_URL_SYNC=$(TEST_DB_URL) -e SECRET_KEY=test-only \
            -e ENVIRONMENT=test -e ALLOWED_HOSTS=test,localhost -e DASHBOARD_SESSION_SECRET=test-only \
            -e 'HMAC_KEYS={"test_app": "test-only-hmac-secret"}'

# The lean test image, rebuilt only when the lock / Dockerfile change; source is over-mounted.
.test-image.stamp: Dockerfile pyproject.toml poetry.lock
	docker build -q --target test -t $(TEST_IMAGE) . >/dev/null
	@touch $@

test-db-up: ## Start the throwaway test Postgres (tmpfs data, own network)
	@docker network inspect $(TEST_NET) >/dev/null 2>&1 || docker network create $(TEST_NET) >/dev/null
	@docker rm -fv $(TEST_DB) >/dev/null 2>&1 || true
	@docker run -d --name $(TEST_DB) --network $(TEST_NET) --tmpfs /var/lib/postgresql/data \
	  -e POSTGRES_USER=test -e POSTGRES_PASSWORD=test -e POSTGRES_DB=test $(TEST_PG) >/dev/null
	@until docker exec -e PGPASSWORD=test $(TEST_DB) psql -h 127.0.0.1 -U test -d test -tAc 'select 1' >/dev/null 2>&1; do sleep 1; done
	@echo "test-db-up: $(TEST_DB) ready on $(TEST_NET)"

test-db-down: ## Stop and wipe the throwaway test Postgres
	@docker rm -fv $(TEST_DB) >/dev/null 2>&1 || true
	@docker network rm $(TEST_NET) >/dev/null 2>&1 || true

test: test-db-up .test-image.stamp guard-registry ## Full pytest suite against the throwaway DB, canonical luxlint pytest config
	@set +e; C=$$(mktemp); trap 'rm -f "$$C"; $(MAKE) -s test-db-down' EXIT INT TERM; \
	docker image inspect $(TEST_IMAGE) >/dev/null 2>&1 || { rm -f .test-image.stamp; $(MAKE) -s .test-image.stamp || exit 1; }; \
	$(GUARD_RUN) $(LUXLINT) --emit-config pytest > "$$C"; \
	docker run --rm --network $(TEST_NET) $(TEST_ENV) -e PYTHONPATH=/app \
	  -v $(PWD):/app -v "$$C":/pytest.ini:ro -w /app $(TEST_IMAGE) \
	  pytest -c /pytest.ini --rootdir /app -p no:cacheprovider; rc=$$?; \
	exit $$rc

arch: guard-registry ## Architecture conformance via luxarch (reads .luxarch.toml)
	@$(GUARD_RUN) $(LUXARCH)

plan: guard-registry ## The full red board — every arch red, phase-ordered + file-clustered
	@$(GUARD_RUN) $(LUXARCH) --plan

audit: guard-registry ## Dependency CVEs (SCA) against the live advisory feed
	@$(GUARD_RUN) -v luxaudit-cache:/root/.cache/trivy $(LUXAUDIT)

# Targeted re-runs — one rule / one file, still on the canonical config (luxarch --doc
# FLEET-BUILD-DEPLOY-STANDARD, "Targeted re-runs"). For iterating; the merge gate is `make check`.
# The emitted config goes to a per-run mktemp file mounted read-only — never a file in the repo.
arch-rule: guard-registry ## luxarch, ONE rule: make arch-rule RULE=fw.no_inline_html
	@[ -n "$(RULE)" ] || { echo "usage: make arch-rule RULE=<rule-id>"; exit 2; }
	@$(GUARD_RUN) $(LUXARCH) --rule $(RULE)

arch-file: guard-registry ## luxarch reds touching ONE file: make arch-file FILE=app/main.py
	@[ -n "$(FILE)" ] || { echo "usage: make arch-file FILE=<path>"; exit 2; }
	@$(GUARD_RUN) $(LUXARCH) --path $(FILE)

lint-file: guard-registry ## ruff on ONE file, canonical config: make lint-file FILE=app/main.py
	@[ -n "$(FILE)" ] || { echo "usage: make lint-file FILE=<path>"; exit 2; }
	@set -e; C=$$(mktemp); trap 'rm -f "$$C"' EXIT INT TERM; \
	$(GUARD_RUN) $(LUXLINT) --emit-config ruff > "$$C"; \
	docker run --rm -v $(PWD):/repo -v "$$C":/cfg.toml:ro -w /repo --entrypoint ruff $(LUXLINT) \
	  check --config /cfg.toml $(FILE)

mypy-file: guard-registry ## mypy on ONE file, canonical config: make mypy-file FILE=app/main.py
	@[ -n "$(FILE)" ] || { echo "usage: make mypy-file FILE=<path>"; exit 2; }
	@set -e; C=$$(mktemp); trap 'rm -f "$$C"' EXIT INT TERM; \
	$(GUARD_RUN) $(LUXLINT) --emit-config mypy > "$$C"; \
	docker run --rm -v $(PWD):/repo -v "$$C":/cfg.ini:ro -w /repo --entrypoint mypy $(LUXLINT) \
	  --config-file /cfg.ini $(FILE)

# Committed guard-status files (.lux*-status.json): the fleet reads these instead of re-running.
# Recipe verbatim from luxarch --doc FLEET-STATUS.
STAMP = python3 -c 'import json,sys,os; d=json.load(open(sys.argv[1])); g=d.get("guard"); g==sys.argv[3] or sys.exit(f"status: {sys.argv[1]} holds {g!r} output, expected {sys.argv[3]!r}; refusing to stamp"); d["commit"]=os.environ["SHA"]; d["generated_at"]=os.environ["TS"]; json.dump(d,open(sys.argv[2],"w"),indent=2)'

status: guard-registry ## Regenerate committed guard-status files (.lux*-status.json) — commit them
	@set -e; export SHA=$$(git rev-parse HEAD) TS=$$(date -u +%FT%TZ); \
	J=$$(mktemp); trap 'rm -f "$$J"' EXIT INT TERM; \
	$(GUARD_RUN) $(LUXLINT)  --json > "$$J" || true; $(STAMP) "$$J" .luxlint-status.json luxlint; \
	$(GUARD_RUN) -e LUXARCH_STATUS_WRITE=1 $(LUXARCH) --json > "$$J" || true; $(STAMP) "$$J" .luxarch-status.json luxarch; \
	$(GUARD_RUN) $(LUXAUDIT) --json > "$$J" || true; $(STAMP) "$$J" .luxaudit-status.json luxaudit; \
	echo "wrote .lux*-status.json at $$SHA — commit them"

# db-verify settings (the emitted block below reads these; set ABOVE it, as it says).
DBV_PG_IMAGE  := postgres:16-alpine
DBV_EXTRA_ENV := -e DATABASE_URL_SYNC=unused -e SECRET_KEY=db-verify-only

# luxarch:db-verify asset v2 - DO NOT edit this marker line; it is how repo.emitted_assets_current knows your copy is current. Re-emit with `luxarch --emit db-verify`.
# ── The migration-chain gate ────────────────────────────────────────────────────────────────────
# Emitted by `luxarch --emit db-verify`. Drop in verbatim and set the five variables above it.
#
# WHY THIS IS EMITTED. FLEET-MAKEFILE-STANDARD used to print this target as a SKELETON —
# `<create a FRESH empty DB>`, `<run alembic upgrade head>` — so every repo invented the shell
# itself. A survey found SEVENTEEN different db-verify implementations across the fleet: some
# `compose exec` into the test stack, some build a throwaway image, some ran a mutable `:dev` tag
# that nothing rebuilt. That divergence was not drift; it was commissioned by a fill-in-the-blanks
# template for the one target that has to orchestrate Docker, Postgres and alembic at once.
#
# WHAT IT PROVES. The suite builds its schema from the chain, so "does the chain run" is answered
# there. What is ONLY answered here is whether the chain's RESULT MATCHES THE MODELS: it migrates a
# genuinely empty database to head and diffs the result against `Base.metadata`, failing on ANY
# structural difference — additive included. A missing table shows up as `add_table`, which a
# destructive-only check (`--emit schema-drift`) waves straight through.
#
# NEVER point this at the create_all test DB: both sides would derive from the same metadata and
# the check goes near-vacuous.

DBV_DB        ?= $(shell basename $(CURDIR))_migverify
DBV_NET       ?= $(DBV_DB)_net
DBV_IMAGE     ?= $(DBV_DB):verify
DBV_PG_IMAGE  ?= postgres:17-alpine
# Any env the app's Settings REQUIRES to import. Values are irrelevant here — this never serves
# traffic — but a missing required setting fails at import and reads as a migration error.
DBV_EXTRA_ENV ?=

# v2 — IT LEAKED A VOLUME EVERY RUN, AND THE DISK FILLED. The postgres image declares a VOLUME, so
# `docker run` minted an anonymous ~58 MB data volume each time, and `docker rm -f` (no `-v`) left it
# behind. Measured: `--rm` does not save it either when the container is force-removed. And every
# recipe line is its own shell, so a FAILING upgrade or verify stopped `make` before the cleanup
# lines ran, leaking the container and network too. ~850 such volumes (~49 GB) filled the build host
# on 2026-10-01. Now: the data directory is a tmpfs (nothing to leak, and faster), and the whole
# target is ONE shell whose `trap` removes the container and network on every exit path.
#
# The image is BUILT, never a registry tag: `:dev`/`:latest` are MOVING pointers, so with the tree
# mounted you would verify today's migrations against yesterday's interpreter and dependencies, which
# fails on correct code and passes on broken code (repo.db_verify_image_is_current). PYTHONPATH=/app,
# not just -w: python puts the SCRIPT's directory on sys.path, not the working dir, so a mounted
# verifier cannot import the models it exists to diff against.
db-verify: ## PROVE the chain builds the schema from EMPTY, no drift vs models
	@set -e; \
	cleanup() { docker rm -fv $(DBV_DB) >/dev/null 2>&1 || true; docker network rm $(DBV_NET) >/dev/null 2>&1 || true; }; \
	trap cleanup EXIT INT TERM; cleanup; \
	docker network create $(DBV_NET) >/dev/null; \
	docker run -d --name $(DBV_DB) --network $(DBV_NET) --tmpfs /var/lib/postgresql/data \
	  -e POSTGRES_USER=$(DBV_DB) -e POSTGRES_PASSWORD=$(DBV_DB) -e POSTGRES_DB=$(DBV_DB) \
	  $(DBV_PG_IMAGE) >/dev/null; \
	until docker exec -e PGPASSWORD=$(DBV_DB) $(DBV_DB) \
	  psql -h 127.0.0.1 -U $(DBV_DB) -d $(DBV_DB) -tAc 'select 1' >/dev/null 2>&1; do sleep 1; done; \
	docker build -q -t $(DBV_IMAGE) . >/dev/null; \
	echo "db-verify: alembic upgrade head (empty -> head)"; \
	docker run --rm --network $(DBV_NET) $(DBV_EXTRA_ENV) \
	  -e DATABASE_URL="postgresql+asyncpg://$(DBV_DB):$(DBV_DB)@$(DBV_DB):5432/$(DBV_DB)" \
	  $(DBV_IMAGE) alembic upgrade head; \
	echo "db-verify: diff the migrated schema against the models"; \
	docker run --rm --network $(DBV_NET) -w /app -v $(CURDIR)/scripts:/scripts:ro \
	  -e PYTHONPATH=/app $(DBV_EXTRA_ENV) \
	  -e DATABASE_URL="postgresql+asyncpg://$(DBV_DB):$(DBV_DB)@$(DBV_DB):5432/$(DBV_DB)" \
	  $(DBV_IMAGE) python /scripts/verify_migration_chain.py

# The machine gate for "is this repo ONBOARDED": wiring + honesty, NOT green.
# See luxarch --doc FLEET-ONBOARDING-STANDARD §5.
onboard-check: guard-registry ## Prove the repo is onboarded: all three guards on + honest + privacy wired
	@set +e; fail=0; \
	$(GUARD_RUN) $(LUXARCH)  --version       >/dev/null || { echo "luxarch not wired";  fail=1; }; \
	$(GUARD_RUN) $(LUXARCH)  --assert-scans  >/dev/null 2>&1 || { echo "luxarch: a rule family scanned NOTHING (hollow green) — point it at real code or remove the surface"; fail=1; }; \
	$(GUARD_RUN) $(LUXLINT)  --preflight               || { echo "mypy tail NOT honest (luxlint --preflight)"; fail=1; }; \
	$(GUARD_RUN) $(LUXAUDIT) 2>&1 | grep -q "scan could not run" && { echo "luxaudit can't scan — supply-chain blind"; fail=1; }; \
	$(GUARD_RUN) $(LUXLINT)  --version       >/dev/null || { echo "luxlint not wired";  fail=1; }; \
	$(GUARD_RUN) $(LUXAUDIT) --version       >/dev/null || { echo "luxaudit not wired"; fail=1; }; \
	[ -f hooks/pre-commit ] || { echo "secret git-hooks NOT wired (luxlint --emit-hooks | sh, commit hooks/)"; fail=1; }; \
	[ ! -d .github/workflows ] || { echo "public CI present — make check is the sole gate (remove .github/workflows)"; fail=1; }; \
	! sed 's/#.*//' Makefile 2>/dev/null | grep -qE '^[[:space:]]+[^#]*\bruff[[:space:]]+format\b' || { echo "Makefile RUNS a bare formatter in a recipe (wrong width) — use 'luxlint --format'"; fail=1; }; \
	$(MAKE) -s gitleaks >/dev/null 2>&1 || { echo "gitleaks found secrets in FULL history — scrub before onboarding is complete"; fail=1; }; \
	[ $$fail -eq 0 ] && echo "onboard-check: all three guards on + honest + privacy wired + history clean ✓" || { echo "onboard-check FAILED"; exit 1; }

# luxarch:gitleaks asset v10 - DO NOT edit this marker line; it is how repo.emitted_assets_current knows your copy is current. Re-emit with `luxarch --emit gitleaks`.
# ── The privacy gate: BOTH surfaces ─────────────────────────────────────────────────────────────
# Emitted by `luxarch --emit gitleaks`. Drop in verbatim.
#
# `gitleaks` scans DIFF CONTENT. A commit's author/committer address lives in the commit object
# HEADER and never appears in a patch, so no content rule can ever match it — it is a surface the
# scanner does not read. A repo reported `no leaks found` over 963 commits while 29 of them carried a
# personal address in both the author and committer fields, and it would have reported exactly the
# same thing after the scrub: identical output, opposite truth. Measured across the fleet, EIGHT
# repos carry a personal address in history and two of them are PUBLIC.

# Commit identities this repo accepts. The fleet account's `users.noreply.github.com` address, plus
# GitHub's own web-UI committer. Widen ONLY for a real outside contributor, with a comment saying who.
# NOT for the org account's real address: a role mailbox in commit metadata is published with every
# clone exactly like a personal one (six fleet repos carried it, one PUBLIC). Its
# omission here is the policy, not an oversight: the answer is the scrub printed below, and the
# repo's agent performs it once the OWNER approves the force-push.
# Anchored on the CLOSING BRACKET, because the compared line is `Name <email>` — not a bare
# address. The first cut allowed `^noreply@github.com$$`, which can NEVER match a
# `Name <email>` line, so the GitHub web-UI identity was silently DENIED and the canonical
# recipe would have refused on any repo carrying a web-UI commit. Measured across the fleet: it
# denied 4 of 6 distinct identity lines instead of the 3 real offenders.
# It was missed because the only repo it was tested on has no web-UI commits, so the broken
# branch never ran. The bracket also closes a substring hole: unanchored,
# `<x@users.noreply.github.com.attacker.test>` would have been allowed.
GIT_IDENTITY_OK ?= <[^>]*users\.noreply\.github\.com>$$|<noreply@github\.com>$$

# The secret scanner, PINNED and MIRRORED in the fleet registry. The fleet bans a moving tag
# everywhere it can see one, and this used to ship `ghcr.io/gitleaks/gitleaks:latest` inside the asset every
# repo adopts verbatim: the privacy gate could not run with ghcr unreachable or the local copy pruned, and
# nothing recorded which scanner said "no leaks found". New detection rules still arrive, through the fleet's
# own mechanism: luxarch bumps this pin in a release, and `repo.emitted_assets_current` tells you to re-emit.
# v5: the HOST is never written here. v4 inlined the private registry, so dropping
# this asset in "verbatim" put the host into a committed Makefile, and on a public repo the fleet's
# own gitleaks disclosure tier refused the commit. The mirror lives beside the guards, so the ref is
# derived from wherever this repo already pulls luxlint (`$(LUXLINT)`, which the scan below needs
# anyway). It works whichever variable holds your guard registry (REGISTRY, LUXARCH_REGISTRY, …).
# Recursive `=` so it resolves at use, whatever order LUXLINT is defined in.
# v9: PINNED BY DIGEST, and buildable off-network. The digest is the scanner's identity; the registry is
# only where it is fetched from. Beside a registry-qualified `$(LUXLINT)` it pulls the fleet mirror; with
# a local guard build (`luxlint:local`, on a machine with no access to the fleet registry, such as an
# airgapped laptop) it pulls the public image. v8 derived `./gitleaks:…` there, an unpullable reference, so the
# privacy gate could not run at all. The mirror and the public image share the digest, so both
# resolve to the same bits, and a tampered or re-tagged copy fails the pull instead of scanning.
GITLEAKS_IMAGE = $(if $(findstring /,$(LUXLINT)),$(dir $(LUXLINT)),zricethezav/)gitleaks:v8.30.1@sha256:c00b6bd0aeb3071cbcb79009cb16a60dd9e0a7c60e2be9ab65d25e6bc8abbb7f

gitleaks: ## secret scan over FULL HISTORY + the commit-identity pass (the hooks cover commit/push)
	@set -e; C=$$(mktemp); trap 'rm -f "$$C"' EXIT INT TERM; \
	docker run --rm -v $(PWD):/repo $(LUXLINT) --emit-config gitleaks > "$$C"; \
	docker run --rm -v $(PWD):/repo -v "$$C":/gl.toml:ro -w /repo \
	  $(GITLEAKS_IMAGE) git /repo -c /gl.toml --redact -v
	@# The identity pass — the half gitleaks structurally cannot do. Cheap: one `git log`.
	@# Walks what THIS repo publishes (branches, tags, HEAD), NOT `--all`: a remote-tracking ref caches the
	@# remote's state, which during a scrub is by definition the un-rewritten history you are about to
	@# force-push over — `--all` refused the verified fix, and any `git fetch` re-armed it.
	@bad=$$(git log --branches --tags HEAD --pretty='%an <%ae>%n%cn <%ce>' 2>/dev/null | sort -u \
	  | grep -vE '$(GIT_IDENTITY_OK)' || true); \
	if [ -n "$$bad" ]; then \
	  echo "REFUSING: a non-fleet identity appears in commit METADATA (author/committer):"; \
	  echo "$$bad" | sed 's/^/    /'; \
	  echo "gitleaks cannot see this — it scans diffs, not commit headers, so it reported no leaks."; \
	  echo "An address here is attached to every affected commit forever, not to one line of one file."; \
	  echo "Scrub per FLEET-ONBOARDING-STANDARD §2: mirror backup -> git filter-repo -> re-verify with"; \
	  echo "  git log --branches --tags HEAD --pretty='%an <%ae>%n%cn <%ce>' | sort -u"; \
	  echo "-> ask the OWNER to approve the force-push, then do it yourself. Never force-push unapproved."; \
	  exit 1; \
	fi

# v6: the STAGED scan the commit hook calls (`hooks/pre-commit` → `make gitleaks-staged`) is part of the
# asset now. v5 shipped only the full-history half, so 9 of 10 adopting repos hand-wrote this target
# and the tenth had none, leaving its pre-commit hook pointing at a missing recipe. If your Makefile
# carries its own `gitleaks-staged`, delete it when you re-emit: this one replaces it.
# v7: `-w /repo` is LOAD-BEARING. Without it git runs outside the repo, falls back to `git diff
# --no-index`, rejects `--staged`, and gitleaks EXITS 0: v6 let a staged secret through while printing
# a git error (measured on a planted GitHub token: v6 exit 0, v7 "leaks found: 1" exit 1).
# v8: the denylist goes to a PER-RUN `mktemp` file, removed on exit. v7 wrote a fixed
# `/tmp/gl.toml` that outlived the run: on a host where commit and push run as different users, the
# next user's redirect was refused (`fs.protected_regular=1`, the Fedora default, blocks O_CREAT on
# another user's file in sticky /tmp even for root), so the privacy gate failed every commit or push
# after a user switch (2 of 2 measured). Two repos scanning at once also shared one file, so one could
# scan with the other's carve-outs. The full-history scan now also passes `-w /repo`, like the staged one.
gitleaks-staged: ## secret scan of the STAGED changes (run by hooks/pre-commit)
	@set -e; C=$$(mktemp); trap 'rm -f "$$C"' EXIT INT TERM; \
	docker run --rm -v $(PWD):/repo $(LUXLINT) --emit-config gitleaks > "$$C"; \
	docker run --rm -v $(PWD):/repo -v "$$C":/gl.toml:ro -w /repo \
	  $(GITLEAKS_IMAGE) protect --staged /repo -c /gl.toml --redact -v

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
	@echo "  make stack-status - Quick health check"
	@echo "  make css         - Build Tailwind CSS"
	@echo "  make css-watch   - Watch and rebuild CSS on changes"
	@echo ""
	@echo "Local Registry ($(REGISTRY)):"
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
# Buildx Setup — the ONE shared fleet builder (emitted asset)
# =============================================================================
# luxarch:buildx-setup asset v2 - DO NOT edit this marker line; it is how repo.emitted_assets_current knows your copy is current. Re-emit with `luxarch --emit buildx-setup`.
BUILDX_BUILDER ?= luxardo-builder
buildx-setup:
	@mkdir -p $(HOME)/.docker
	@[ -f $(HOME)/.docker/buildkitd.toml ] || printf '[worker.oci]\n  gc = true\n  [[worker.oci.gcpolicy]]\n    keepBytes = "20GB"\n    all = true\n' > $(HOME)/.docker/buildkitd.toml
	@docker buildx inspect $(BUILDX_BUILDER) >/dev/null 2>&1 || \
	  docker buildx create --name $(BUILDX_BUILDER) --driver docker-container \
	    --buildkitd-config $(HOME)/.docker/buildkitd.toml --use
	@docker buildx use $(BUILDX_BUILDER)
	@strays=$$(docker buildx ls 2>/dev/null | awk '$$2=="docker-container"{print $$1}' \
	  | grep -v '^\\_' | sed 's/\*$$//' | grep -vxF "$(BUILDX_BUILDER)" | tr '\n' ' '); \
	if [ -n "$$strays" ] && [ -z "$(ALLOW_STRAY_BUILDERS)" ]; then \
	  echo "REFUSING: stray per-project buildx builders are running: $$strays"; \
	  echo "Remove them:  docker buildx rm $$strays"; \
	  exit 1; \
	fi

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
	@test -n "$${REGISTRY_PASSWORD}" || { echo "REGISTRY_PASSWORD not set (expected in .env.build)"; exit 1; }
	@printf '%s' "$${REGISTRY_PASSWORD}" | docker login $(EXTERNAL_REGISTRY) -u $(REGISTRY_USER) --password-stdin
	docker buildx build --platform linux/amd64 \
		$(BUILD_ARGS) \
		-t $(EXTERNAL_IMAGE):$(BUILD_VERSION) \
		$(CACHE_FLAG) --push .
	@docker logout $(EXTERNAL_REGISTRY)
	@echo "✅ Pushed $(EXTERNAL_IMAGE):$(BUILD_VERSION)"

external-build-latest: buildx-setup css
	@echo "Building and pushing to external registry with :latest..."
	@test -n "$${REGISTRY_PASSWORD}" || { echo "REGISTRY_PASSWORD not set (expected in .env.build)"; exit 1; }
	@printf '%s' "$${REGISTRY_PASSWORD}" | docker login $(EXTERNAL_REGISTRY) -u $(REGISTRY_USER) --password-stdin
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

stack-status: ## Container status + health of the local stack
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
# Production Deployment (prod node via jump host; set in Makefile.local)
# =============================================================================
# The prod node and its jump host come from Makefile.local.
PROD_JUMP ?=
PROD_HOST ?=
PROD_PATH ?= /opt/luxardolabs/luxanalytics
PROD_SSH := ssh $(PROD_JUMP) "ssh $(PROD_HOST)

prod-push:
	@echo "Pushing deploy config to production..."
	@# Streamed through both ssh hops: no staging file on any host.
	@tar -czf - -C deploy/prod . | $(PROD_SSH) 'mkdir -p $(PROD_PATH) && tar -xzf - -C $(PROD_PATH)/'"
	@echo "✅ Deploy config pushed to $(PROD_PATH)"

prod-deploy:
	@echo "Deploying LuxAnalytics $(BUILD_VERSION) to production..."
	@# The password crosses both ssh hops on STDIN, never on a remote command line (where ps shows it).
	@set -e; test -n "$${REGISTRY_PASSWORD}" || { echo "REGISTRY_PASSWORD not set (expected in .env.build)"; exit 1; }; \
	printf '%s' "$${REGISTRY_PASSWORD}" | $(PROD_SSH) 'docker login $(EXTERNAL_REGISTRY) -u $(REGISTRY_USER) --password-stdin'"; \
	$(PROD_SSH) 'cd $(PROD_PATH) && docker compose --env-file .env.prod pull && docker compose --env-file .env.prod up -d; rc=\$$?; docker logout $(EXTERNAL_REGISTRY); exit \$$rc'"
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
	@$(PROD_SSH) 'cat > /opt/nginx/conf.d/analytics.luxardolabs.com.conf'" < deploy/prod/analytics.luxardolabs.com.conf
	@$(PROD_SSH) 'docker exec nginx nginx -s reload'"
	@echo "✅ Nginx config updated and reloaded"

prod-migrate:
	@echo "Running migrations on production..."
	@$(PROD_SSH) 'docker exec luxanalytics_app alembic upgrade head'"
	@echo "✅ Migrations complete"

prod-backup:
	@echo "Backing up production database..."
	@# pg_dump streams back over both ssh hops into backups/ (gitignored): no staging file on any host.
	@set -e; mkdir -p backups; f=backups/luxanalytics-backup-$$(date +%Y%m%d).sql.gz; \
	$(PROD_SSH) 'set -o pipefail; docker exec luxanalytics_db pg_dump -U luxanalytics luxanalytics | gzip'" > "$$f.part"; \
	mv "$$f.part" "$$f"
	@echo "✅ Backup saved to backups/"

prod-version:
	@$(PROD_SSH) 'docker inspect luxanalytics_app --format \"{{.Config.Image}}\" 2>/dev/null || echo not running'"

prod-release: external-build prod-push prod-deploy
	@echo "✅ Released $(BUILD_VERSION) to production"
