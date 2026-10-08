# =============================================================================
# LuxAnalytics — analytics event collector API (FastAPI + PostgreSQL + Redis)
# =============================================================================

# Private topology (registry hosts, prod node, registry credential) is kept OUT of this tree:
# set it in an untracked Makefile.local (see Makefile.local.example). Included FIRST so its
# values win over the empty defaults below.
-include Makefile.local

# =============================================================================
# Configuration
# =============================================================================
APP_NAME := luxanalytics

# The fleet registry (Makefile.local): the ONE artifact hub. It holds this app's images (named by the
# image block below) AND the three guard images; production pulls from it too.
REGISTRY ?=

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
LUXARCH_VERSION  := 0.273.0
LUXLINT_VERSION  := 0.62.1
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
        arch-rule arch-file lint-file mypy-file status db-verify \
        audit gitleaks gitleaks-staged onboard-check \
        network up down restart logs logs-app logs-db shell shell-db migrate migrate-down \
        migrate-create clean stack-status health ps backup restore work quick seed-sdk-app css-watch check-env \
        publish-sha release gh-release dev-deploy dev-pin test-build \
        buildx-setup version \
        prod-sync prod-pin prod-deploy prod-restart prod-stop prod-logs prod-status prod-shell prod-shell-db \
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
DBV_EXTRA_ENV := -e ENVIRONMENT=test -e DATABASE_URL_SYNC=unused -e SECRET_KEY=db-verify-only -e DASHBOARD_PASSWORD=db-verify-only

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

# luxarch:gitleaks asset v11 - DO NOT edit this marker line; it is how repo.emitted_assets_current knows your copy is current. Re-emit with `luxarch --emit gitleaks`.
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
# v11: the noreply address is `<local@users.noreply.github.com>`, and the local part has no `@`. v10's
# `<[^>]*users…` admitted `<dev.real@gmail.com.users.noreply.github.com>`, a real address in the clear.
GIT_IDENTITY_OK ?= <[^@<> ]+@users\.noreply\.github\.com>$$|<noreply@github\.com>$$

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
	@echo "Images ($(REGISTRY)):"
	@echo "  make publish-sha   - Build + scan + push this commit as :sha-<commit>"
	@echo "  make release       - Cut VERSION: build + scan + push :$(VERSION), then the GitHub Release"
	@echo "  make version       - Show the version and image refs"
	@echo ""
	@echo "Production:"
	@echo "  make prod-release  - release + prod-deploy (pin TAG=$(VERSION), sync, pull, up)"
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
# Images, deploy tags and the release (emitted asset — never hand-edit)
# =============================================================================
# luxarch:image-block asset v10 - DO NOT edit this marker line; it is how repo.emitted_assets_current knows your copy is current. Re-emit with `luxarch --emit image-block`.
# ── Images: ONE naming scheme, and the NAME declares what the image IS ──────────────────────────
# Emitted by `luxarch --emit image-block`. Two axes, both readable from the name alone:
#
#   Registry-qualified  ->  DEPLOY artifact  ->  built AND PUSHED
#   Bare (no registry)  ->  LOCAL artifact   ->  built from source every run, NEVER pushed
#
#   Immutable tag (:$(VERSION), :sha-…)  ->  a stack MAY pin it
#   Moving tag (:dev, :latest)           ->  a human alias; NOTHING pins it, NOTHING builds FROM it
#
# **Immutable tags are the point.** "A deployable never rolls" is why `:latest` is banned as a
# deploy tag — and `:dev` rolls identically. Every `docker push` of `:dev` silently changes what
# every stack pinned to it will run on next restart, so a moving tag cannot be a deployment record:
# you cannot say which bits are in dev, and you cannot roll back to them. One build -> one permanent
# identifier -> environments reference THAT. The sha costs nothing: this repo already computes it
# for BUILD_COMMIT.
#
# **A local-only image must not wear a registry name.** One repo's compose pulled
# `$(REGISTRY)/luxardolabs/<app>:dev` with no `build:` fallback, its Makefile never pushed it,
# the registry held ZERO tags, and a routine `docker image prune` took the service down with
# nothing to re-pull. The name promised a registry artifact; only a local one existed.
#
# Enforced by `repo.image_name_declares_provenance`.

REGISTRY      ?=
IMAGE_NAME    := luxardolabs/$(notdir $(CURDIR))
# BASE — no tag; every tag composes from it. The comment sits ABOVE the value, never after it:
# GNU make keeps the whitespace between a value and an inline `#`, so `IMAGE := …/repo   # note`
# defines IMAGE *with trailing spaces*, and every tag built from it — `…/repo   :0.1.0` — is an
# invalid docker reference. This block shipped that way from 0.197.0 and `make release` could not
# run at all; no repo had executed it, so three green rules stood over a recipe that was broken on
# its first line. Locked by test_emitted_makefiles_have_no_inline_comment_on_an_assignment.
IMAGE         := $(REGISTRY)/$(IMAGE_NAME)
COMMIT        := $(shell git rev-parse --short=12 HEAD 2>/dev/null || echo unknown)

# IMMUTABLE deploy tags — the ONLY tags a stack may pin (`TAG=` in .env.<env>).
#   VERSION_IMAGE = cut releases;  SHA_IMAGE = every other build, addressable, no ceremony.
VERSION_IMAGE := $(IMAGE):$(VERSION)
SHA_IMAGE     := $(IMAGE):sha-$(COMMIT)

# MOVING alias — for a human reading `docker images`. Never pinned by a stack, never a build input,
# never a base. If you delete this line nothing breaks; that is the test of an alias.
DEV_ALIAS     := $(IMAGE):dev

# LOCAL verification image — BARE on purpose: it cannot be pushed by accident and cannot be
# mistaken for a deployable. Rebuilt from source every run, so the suite never inherits a stale
# interpreter or stale deps. It is NOT `:dev`; a repo that tested on `:dev` kept failing correct
# code against a 13-day-old Python 3.13 image after the tree had moved to 3.14.
TEST_IMAGE    := $(notdir $(IMAGE_NAME)):test

# Where the image is built from. v9: v1-v8 built `.` with `./Dockerfile`, so a monorepo whose app builds
# from `apps/backend/` could only adopt the block by editing it. Set these above the block when the app
# is not at the root, e.g. `BUILD_CONTEXT := apps/backend`. Keep the context as narrow as the app: in a
# repo that holds data which must never ship, a narrow context is what keeps that data out of every
# image, structurally, rather than one `.dockerignore` pattern away.
BUILD_CONTEXT ?= .
DOCKERFILE    ?= $(BUILD_CONTEXT)/Dockerfile

# ── Deploy: build AND push, always together ─────────────────────────────────────────────────────
# Never split them. A `docker build` of a registry-qualified tag with no matching push is what
# leaves a deployable name pointing at nothing.

# Named `release`, not `publish`. An earlier cut of this block called it `publish` while
# `--emit release-skill` step 11, `--doc FLEET-RELEASE-PROCESS` §8 and `repo.github_release_wired`
# all said `release` — so a repo that correctly adopted THIS asset ended up with no `release` target
# and the rule stopped looking. Adopting the newer asset was what made the older rule go quiet.
# One name, and `release` is the one the skill, the standard, the rule and every
# fleet Makefile already use.
# A `sha-<commit>` tag is immutable only if the bits ARE that commit. `git rev-parse HEAD` says nothing
# about the working tree: build with uncommitted changes and the registry holds an image named after
# commit X that contains X plus whatever was dirty, which is indistinguishable afterwards from a
# truthful one and worse than `:dev`, which is at least honestly ambiguous. One repo published
# `sha-b55e9a7b25ed` carrying a lockfile that commit does not have: bump deps, `make dev-build` to see
# it works, commit after. That is the routine order, and this recipe invited it. Untracked
# files count, because they are in the build context. No escape hatch: every fleet repo builds and PUSHES,
# and a stack runs what was pushed, so commit first. `make test-build` is for the TEST suite, not the stack.
# The candidate gate, used by every target that pushes: scan the exact bits just built, mount-only,
# and refuse the push on any fixable HIGH/CRITICAL (luxaudit >= 0.13.0 in LUXAUDIT_IMAGE or LUXAUDIT).
# `make audit`'s image leg reads what the registry already holds, so it can only clear AFTER a release;
# as the gate it deadlocked. This is the gate. (v5)
define scan_candidate
	@set -e; ref='$(or $(LUXAUDIT_IMAGE),$(LUXAUDIT))'; \
	if [ -z "$$ref" ]; then echo "REFUSING: set LUXAUDIT_IMAGE to the pinned luxaudit; the candidate must be scanned before it is pushed"; exit 2; fi; \
	T=$$(mktemp); trap 'rm -f "$$T"' EXIT INT TERM; \
	docker save $(1) -o "$$T"; chmod 644 "$$T"; \
	docker run --rm -v $(PWD):/repo -v luxaudit-cache:/root/.cache/trivy -v "$$T":/candidate.tar:ro \
	  "$$ref" --image-archive /candidate.tar --image-label $(1)
endef

define refuse_dirty_tree
	@if [ -n "$$(git status --porcelain 2>/dev/null)" ]; then \
	  echo "REFUSING: the working tree is dirty, so sha-$(COMMIT) would not describe these bits:"; \
	  git status --short | sed 's/^/    /'; \
	  echo "Commit first, then deploy what you pushed (make dev-deploy). make test-build is for the TEST suite only; it cannot run the stack."; \
	  exit 1; \
	fi
endef

# Every commit's deployable: build, scan, push `:sha-<commit>` (what a dev/demo stack pins) and move the
# `:dev` alias. NEVER the version tag. v6: v5's `dev-deploy` ran `release`, which pushed `:$(VERSION)`
# on every run, so the first dev deploy after a version shipped overwrote that RELEASED tag with
# unreleased code (in one repo, prod's `:2026.09.0` silently became another
# commit's bits, caught by hand before the next prod pull).
publish-sha: ## Build + scan + PUSH this commit as :sha-<commit> (and move the :dev alias) — never the version
	$(refuse_dirty_tree)
	docker build --target production $(BUILD_ARGS) -f $(DOCKERFILE) -t $(SHA_IMAGE) $(BUILD_CONTEXT)
	$(call scan_candidate,$(SHA_IMAGE))
	docker push $(SHA_IMAGE)
	@# The alias moves LAST and carries nothing: it is a label on an already-published artifact.
	docker tag $(SHA_IMAGE) $(DEV_ALIAS) && docker push $(DEV_ALIAS)
	@echo "deploy it with:  make dev-deploy   (or: make dev-pin TAG=sha-$(COMMIT))"

# The GitHub Release: this version's user-facing notes on the repo's /releases page (--doc
# FLEET-RELEASE-PROCESS §9, `repo.github_release_wired`). v8: v1-v7 had no step for it, so an exact copy
# of this DO-NOT-EDIT block was red on that rule and the only ways to green were editing the block or a
# target outside `release` that nothing runs. `release` now runs it last, and checks everything it needs
# BEFORE the first push, against the REMOTE: `gh release create --verify-tag` needs the tag on GitHub, so
# a tag that exists only locally would let the image push and the Release fail. If the Release step
# still fails, re-run `make gh-release` alone: it is idempotent, and it needs the pushed tag, not HEAD
# (`release` refuses an already-released version, and HEAD moves on after the release commit).
# RELEASE_NOTES is found, not assumed: `<app>/release_notes/$(VERSION).md` wherever the app lives, up to
# three directories down (`app/`, `src/<pkg>/`, `apps/backend/app/`). Set it above this block only when
# more than one app ships notes.
RELEASE_NOTES ?= $(wildcard release_notes/$(VERSION).md */release_notes/$(VERSION).md */*/release_notes/$(VERSION).md */*/*/release_notes/$(VERSION).md)

define gh_release_preflight
	@command -v gh >/dev/null 2>&1 || { echo "REFUSING: the GitHub CLI (gh) is not installed; the release publishes its notes with it"; exit 1; }
	@gh auth status >/dev/null 2>&1 || { echo "REFUSING: gh is not authenticated (gh auth login)"; exit 1; }
	@set -- $(RELEASE_NOTES); if [ $$# -ne 1 ]; then \
	  echo "REFUSING: need exactly ONE release notes file for $(VERSION), found $$#: $(or $(RELEASE_NOTES),none)"; \
	  echo "Write <app>/release_notes/$(VERSION).md (luxarch --emit release-notes-guide), or set RELEASE_NOTES above this block."; \
	  exit 1; \
	fi
	@git ls-remote --exit-code --tags origin "refs/tags/v$(VERSION)" >/dev/null 2>&1 || { \
	  echo "REFUSING: v$(VERSION) is not on origin; the GitHub Release hangs off the pushed tag:"; \
	  echo "    git tag -a v$(VERSION) -m v$(VERSION) && git push origin v$(VERSION)"; exit 1; }
endef

gh-release: ## Publish the GitHub Release for v$(VERSION) from its release notes (idempotent; `release` runs it)
	$(gh_release_preflight)
	@if gh release view "v$(VERSION)" >/dev/null 2>&1; then \
	  echo "GitHub Release v$(VERSION) already exists, skipped (a Release, like its tag, is immutable)"; \
	else \
	  gh release create "v$(VERSION)" --verify-tag --title "$(VERSION)" --notes-file $(RELEASE_NOTES); \
	fi

# Cut VERSION: the immutable release tag prod pins. Refuses when that version is ALREADY released, in
# the registry or as a git tag at another commit: a released version is never re-pushed, not even from
# its own commit (a rebuild is different bytes under a name prod already runs). Bump VERSION instead.
release: ## Cut VERSION: build + scan + PUSH :sha-<commit> AND :$(VERSION), then the GitHub Release (refuses an already-released VERSION)
	@if docker manifest inspect $(VERSION_IMAGE) >/dev/null 2>&1; then \
	  echo "REFUSING: $(VERSION_IMAGE) is already RELEASED. A released version is immutable: prod pins it."; \
	  echo "Bump VERSION for a new release; deploy this commit to dev with make dev-deploy (:sha-$(COMMIT))."; \
	  echo "If its GitHub Release is missing (the last step failed), run: make gh-release"; \
	  exit 1; \
	fi
	@# Fails CLOSED: `manifest inspect` exits 1 for "no such manifest" AND for an unreachable registry,
	@# so only the registry's own not-found answer reads as unreleased (a DNS blip must not let a
	@# re-push through).
	@out=$$(docker manifest inspect $(VERSION_IMAGE) 2>&1) || case "$$out" in \
	  *[Nn]"o such manifest"*|*"manifest unknown"*) ;; \
	  *) echo "REFUSING: cannot verify $(VERSION_IMAGE) is unreleased: $$out"; exit 1 ;; \
	esac
	@t=$$(git rev-parse -q --verify "refs/tags/v$(VERSION)^{commit}" 2>/dev/null); \
	if [ -n "$$t" ] && [ "$$t" != "$$(git rev-parse HEAD)" ]; then \
	  echo "REFUSING: v$(VERSION) is already tagged at $$t, not HEAD: bump VERSION."; exit 1; \
	fi
	$(refuse_dirty_tree)
	@t=$$(git rev-parse -q --verify "refs/tags/v$(VERSION)^{commit}" 2>/dev/null); \
	if [ "$$t" != "$$(git rev-parse HEAD)" ]; then \
	  echo "REFUSING: v$(VERSION) is not tagged at HEAD. Tag and push it first:"; \
	  echo "    git tag -a v$(VERSION) -m v$(VERSION) && git push origin v$(VERSION)"; exit 1; \
	fi
	$(gh_release_preflight)
	docker build --target production $(BUILD_ARGS) -f $(DOCKERFILE) -t $(SHA_IMAGE) -t $(VERSION_IMAGE) $(BUILD_CONTEXT)
	$(call scan_candidate,$(SHA_IMAGE))
	docker push $(SHA_IMAGE)
	docker push $(VERSION_IMAGE)
	docker tag $(SHA_IMAGE) $(DEV_ALIAS) && docker push $(DEV_ALIAS)
	@$(MAKE) --no-print-directory gh-release
	@echo "released $(VERSION_IMAGE); pin prod to TAG=$(VERSION)"

# ── Point an environment at a build ─────────────────────────────────────────────────────────────
# Immutable tags move a burden: a NEW tag per build has to actually reach the stack. This block used
# to end by telling a human to go hand-edit a gitignored file — advice beside a working button, and
# it is how a dev node ends up still serving last week's sha while everyone believes otherwise.
#
# The tag is PERSISTED into .env.<env>, not passed in the deploying shell, because the stack has to
# come back after a reboot: compose reads `${TAG:?}`, so a tag that lived only in one shell leaves a
# plain `docker compose up` on that node unable to start the stack at all. Exactly ONE line of that
# file is rewritten in place; nothing else is read, printed or reordered, because it holds secrets.
#
# `.env.prod` takes the same call from whatever `prod-deploy` does over SSH — the pinning is shared,
# the transport is node-specific and stays in the repo.

TAG ?= sha-$(COMMIT)

# Which compose profiles the dev stack runs. v8: v7 restarted with a bare `up -d`, and in a compose.yml
# where every service carries `profiles:` (the fleet's one-file model) that selects NO service unless
# something declares the stack: compose exits 0 and `dev-deploy` reported success over a stack it never
# restarted. Declare it ONE of two ways (`repo.compose_up_selects_services` checks either):
#   - COMPOSE_PROFILES=<profiles> in .env.dev, declared in the committed .env.example (leave this empty);
#   - or set the profiles above this block: `DEV_PROFILES := dev`. A `--profile` REPLACES the
#     env file's COMPOSE_PROFILES, so list every profile the dev stack needs.
# A service with no `profiles:` starts either way.
DEV_PROFILES ?=

define pin_env_tag
	f='$(1)'; t='$(2)'; \
	[ -f "$$f" ] || { echo "$$f is missing — copy .env.example and fill it in first"; exit 1; }; \
	tmp=$$(mktemp); trap 'rm -f "$$tmp"' EXIT; \
	if grep -qE '^[[:space:]]*TAG=' "$$f"; then \
	  awk -v t="$$t" '/^[[:space:]]*TAG=/ && !d {print "TAG=" t; d=1; next} {print}' "$$f" > "$$tmp"; \
	else \
	  cp "$$f" "$$tmp" && printf 'TAG=%s\n' "$$t" >> "$$tmp"; \
	fi; \
	[ -s "$$tmp" ] || { echo "refusing to write an empty $$f"; exit 1; }; \
	o=$$(wc -l < "$$f"); n=$$(wc -l < "$$tmp"); \
	[ "$$n" -ge "$$o" ] || { echo "refusing: rewriting $$f lost lines ($$o -> $$n)"; exit 1; }; \
	cat "$$tmp" > "$$f"; \
	echo "$$f: TAG=$$t"
endef

# v10: a deploy ends by probing what it deployed (`luxarch --emit smoke`, pasted below this block). A
# deploy that restarted the stack is not one that works: the suite runs in-process and cannot see the
# proxy, the server, the production image or a standalone entry point.
dev-deploy: ## Build+push THIS commit (:sha-…, never the version), pin .env.dev to it, restart the dev stack, smoke it
	@$(MAKE) --no-print-directory publish-sha
	@$(MAKE) --no-print-directory dev-pin TAG=sha-$(COMMIT)
	@$(MAKE) --no-print-directory smoke

# The rollback path, and the only one that does not build: name a tag you already published.
# The registry is checked FIRST because the alternative is the outage above — a stack pinned to a
# name the registry never held, discovered at the next restart when there was nothing to re-pull.
dev-pin: ## Point the dev stack at an ALREADY-PUBLISHED tag and restart it (rollback path)
	@docker manifest inspect $(IMAGE):$(TAG) >/dev/null 2>&1 || \
	  { echo "$(IMAGE):$(TAG) is not in the registry — publish it before pinning a stack to it"; exit 1; }
	@$(call pin_env_tag,.env.dev,$(TAG))
	docker compose --env-file .env.dev $(foreach p,$(DEV_PROFILES),--profile $(p)) up -d

# ── Verify: build from source, locally, every run ───────────────────────────────────────────────
# The test image must carry the SAME app layers production ships, plus the dev group. Two Dockerfile
# shapes do that, and either is canonical:
#   - `FROM production AS test`, putting back what production stripped (`python -m ensurepip`)
#     before `poetry install --with dev`;
#   - one shared `app` stage that both `test` and `production` build FROM, when production also
#     strips poetry. v3 said only "FROM production", which a production stage hardened per
#     FLEET-BUILD-DEPLOY-STANDARD (no pip/poetry in the runtime) cannot satisfy literally.
# Either way the Dockerfile needs a stage named `test`: this target builds it.

test-build: ## Build the LOCAL test image from source (never pushed, never a deploy tag)
	@docker build --target test $(BUILD_ARGS) -f $(DOCKERFILE) -t $(TEST_IMAGE) $(BUILD_CONTEXT) >/dev/null

version: ## Show the version and the image refs this commit builds
	@echo "Version:  $(VERSION)"
	@echo "Commit:   $(COMMIT)"
	@echo "Release:  $(VERSION_IMAGE)"
	@echo "This sha: $(SHA_IMAGE)"

# The dev stack is reached the way a browser reaches it: the dev nginx publishes :4000 over HTTPS
# with a self-signed certificate (hence -k). /health is internal by design (the proxy returns 404),
# so the build check reads /login, whose stylesheet cache-buster is the running BUILD_COMMIT.
SMOKE_URL ?= https://localhost:4000
SMOKE_CURL_OPTS ?= -k
SMOKE_HEALTH_PATH ?= /login
# luxarch:smoke asset v3 - DO NOT edit this marker line; it is how repo.emitted_assets_current knows your copy is current. Re-emit with `luxarch --emit smoke`.
# ── Smoke: probe the DEPLOYED stack from outside, after every dev deploy ──────────────────────────
# Emitted by `luxarch --emit smoke`; paste below the image block. `make test` runs the app in-process
# against its test stack, so it cannot see the proxy, the server, the production image or an entry
# point other than the app's. One repo shipped four bugs under a green `make check` that only a probe
# of the deployed stack found: a rate limiter that took the host down, a proxy serving an unstyled UI,
# a standalone script that crashed on a circular import in the production image, and a Host-header
# defence no test exercised. Each probe below is aimed at one of them. `make dev-deploy` runs it
# (`repo.stack_smoke_wired`); a FAIL fails the deploy, and a probe that cannot run says NOT RUN and
# why, never a silent pass.
#
# Settings are `?=` defaults: override them above this block (or in Makefile.local for a host name).

# Setting: where the dev stack is reachable from the build host (e.g. https://dev.example.com) ---
SMOKE_URL ?=
# Setting: the health path; its response must name the build's commit (any field name) -------
SMOKE_HEALTH_PATH ?= /health
# Setting: a static asset the image serves (empty only if the app serves no static files) -----
SMOKE_STATIC_PATH ?= /static/css/app.css
# Setting: a path that needs authentication; the token comes from the SMOKE_AUTH_TOKEN env var -
SMOKE_AUTH_PATH ?=
# Setting: the package directory of standalone entry points, and the image's import root -------
SMOKE_SCRIPTS_DIR ?= app/scripts
SMOKE_IMPORT_ROOT ?= .
# Setting: entry-point MODULES outside that directory. The default is every `*_main` module at the
# app package's root, where the layout standard (fw.module_homes) puts a service's entry points
# (`app.portal_main`); list them yourself for any other place (v3) ---------------------------
SMOKE_ENTRY_MODULES ?= $(basename $(subst /,.,$(patsubst $(SMOKE_IMPORT_ROOT)/%,%,$(wildcard $(SMOKE_IMPORT_ROOT)/app/*_main.py))))
# Setting: the env file the dev stack runs with; each entry point is imported with it, because a
# module that builds the app's settings needs the config the deployed container has (v2) ---------
SMOKE_ENV_FILE ?= .env.dev
# Setting: extra `docker run` options for the entry-point imports (e.g. -e NAME=value) ----------
SMOKE_RUN_OPTS ?=
# Setting: extra curl options (e.g. --cacert <file> for a private CA) -------------------------
SMOKE_CURL_OPTS ?=

smoke: ## Probe the deployed dev stack: build commit, static asset, forged Host refused, auth, standalone entry points
	@set -u; fail=0; \
	[ -n "$(SMOKE_URL)" ] || { echo "REFUSING: set SMOKE_URL to the dev stack's address (Makefile.local)"; exit 2; }; \
	probe() { curl -sS --max-time 15 $(SMOKE_CURL_OPTS) -o "$$B" -w '%{http_code} %{content_type}' "$$@" 2>/dev/null || echo "000 -"; }; \
	B=$$(mktemp); trap 'rm -f "$$B"' EXIT INT TERM; \
	sha=$$(git rev-parse --short=7 HEAD 2>/dev/null || true); \
	r=$$(probe "$(SMOKE_URL)$(SMOKE_HEALTH_PATH)"); \
	if [ -z "$$sha" ]; then echo "FAIL  cannot read this checkout's commit (git rev-parse failed), so the deployed build cannot be checked"; fail=1; \
	elif [ "$${r%% *}" = 200 ] && grep -q "$$sha" "$$B"; then echo "PASS  health names this commit ($$sha)"; \
	else echo "FAIL  $(SMOKE_HEALTH_PATH): $$r, and the response does not name $$sha: the stack is not running this build"; fail=1; fi; \
	if [ -n "$(SMOKE_STATIC_PATH)" ]; then \
	  r=$$(probe "$(SMOKE_URL)$(SMOKE_STATIC_PATH)"); ctype=$${r#* }; \
	  case "$(SMOKE_STATIC_PATH)" in *.css) want=text/css;; *.js|*.mjs) want=javascript;; *) want=;; esac; \
	  if [ "$${r%% *}" = 200 ] && [ -s "$$B" ] && ! grep -qi '<html' "$$B" && { [ -z "$$want" ] || case "$$ctype" in *"$$want"*) true;; *) false;; esac; }; then echo "PASS  static asset served ($(SMOKE_STATIC_PATH), $$ctype)"; \
	  else echo "FAIL  $(SMOKE_STATIC_PATH): $$r: the proxy or image does not serve the built asset as $${want:-a file} (a browser refuses a stylesheet or script with the wrong type)"; fail=1; fi; \
	else echo "NOT RUN  static asset: SMOKE_STATIC_PATH is empty (only right for an app that serves no static files)"; fi; \
	r=$$(probe -H "Host: smoke-forged.invalid" "$(SMOKE_URL)$(SMOKE_HEALTH_PATH)"); \
	case "$${r%% *}" in 2??|3??) echo "FAIL  a forged Host header was answered ($$r): the trusted-host defence is not on in the real stack"; fail=1;; \
	  000) echo "PASS  forged Host refused (connection rejected)";; \
	  *) echo "PASS  forged Host refused ($${r%% *})";; esac; \
	if [ -n "$(SMOKE_AUTH_PATH)" ]; then \
	  r=$$(probe "$(SMOKE_URL)$(SMOKE_AUTH_PATH)"); \
	  case "$${r%% *}" in 401|403) echo "PASS  $(SMOKE_AUTH_PATH) refuses a request with no credentials ($${r%% *})";; \
	    *) echo "FAIL  $(SMOKE_AUTH_PATH) answered a request with NO credentials ($$r): it is not protected"; fail=1;; esac; \
	  if [ -z "$${SMOKE_AUTH_TOKEN:-}" ]; then echo "FAIL  SMOKE_AUTH_PATH is set but SMOKE_AUTH_TOKEN is not in the environment"; fail=1; \
	  else r=$$(probe -H "Authorization: Bearer $${SMOKE_AUTH_TOKEN}" "$(SMOKE_URL)$(SMOKE_AUTH_PATH)"); \
	    if [ "$${r%% *}" = 200 ]; then echo "PASS  authenticated request ($(SMOKE_AUTH_PATH))"; \
	    else echo "FAIL  authenticated $(SMOKE_AUTH_PATH): $$r"; fail=1; fi; fi; \
	else echo "NOT RUN  authenticated request: SMOKE_AUTH_PATH is empty"; fi; \
	n=0; mods=; \
	if [ -d "$(SMOKE_SCRIPTS_DIR)" ]; then for f in $$(find "$(SMOKE_SCRIPTS_DIR)" -name '*.py' ! -name '__init__.py' | sort); do \
	  rel=$$(realpath --relative-to="$(SMOKE_IMPORT_ROOT)" "$$f"); mods="$$mods $$(printf '%s' "$${rel%.py}" | tr / .)"; done; fi; \
	mods=$$(printf '%s\n' $$mods $(SMOKE_ENTRY_MODULES) | sort -u); \
	if [ -n "$$mods" ]; then \
	  envf=; [ -n "$(SMOKE_ENV_FILE)" ] && [ -f "$(SMOKE_ENV_FILE)" ] && envf="--env-file=$(SMOKE_ENV_FILE)"; \
	  if ! docker image inspect "$(SHA_IMAGE)" >/dev/null 2>&1 && ! docker pull -q "$(SHA_IMAGE)" >/dev/null 2>&1; then \
	    echo "FAIL  entry points: $(SHA_IMAGE) is neither built here nor pullable, so no module could be imported (deploy this commit first)"; fail=1; n=-1; \
	  else for mod in $$mods; do \
	    n=$$((n + 1)); \
	    if err=$$(docker run --rm $$envf $(SMOKE_RUN_OPTS) --entrypoint python "$(SHA_IMAGE)" -c "import $$mod" 2>&1 >/dev/null); then echo "PASS  $$mod imports on its own in the production image"; \
	    else fail=1; why=$$(printf '%s\n' "$$err" | grep -E '^[A-Za-z_][A-Za-z0-9_.]*(Error|Exception|Exit)\b' | tail -n 1); why=$${why:-$$(printf '%s\n' "$$err" | grep -v '^[[:space:]]*$$' | tail -n 1)}; \
	      case "$$err" in *ImportError*|*ModuleNotFoundError*|*"circular import"*) echo "FAIL  $$mod does not import on its own in $(SHA_IMAGE) (a circular or missing import the app's own import order hides): $$why";; \
	        *) echo "FAIL  $$mod raised at import in $(SHA_IMAGE): $$why"; [ -n "$$envf" ] || echo "      no env file was passed ($(SMOKE_ENV_FILE) not found): set SMOKE_ENV_FILE to the file the stack runs with";; esac; fi; \
	  done; fi; \
	fi; \
	[ "$$n" -ne 0 ] || echo "NOT RUN  entry points: no module under $(SMOKE_SCRIPTS_DIR) and no SMOKE_ENTRY_MODULES"; \
	exit $$fail

# ── make test settings (luxarch --emit test-block, below). The suite runs against the compose
# `test` profile: a throwaway Postgres and Redis (the services prod runs; repo.test_stack_parity),
# isolated per run. Values are throwaway; the conftest refuses any database or Redis that does not
# look test-scoped.
TEST_SERVICES := db-test redis-test
TEST_DB_URL   := postgresql+asyncpg://test:test@db-test:5432/test
TEST_ENV := -e TEST_DATABASE_URL=$(TEST_DB_URL) -e DATABASE_URL=$(TEST_DB_URL) \
            -e DATABASE_URL_SYNC=$(TEST_DB_URL) -e REDIS_URL=redis://redis-test:6379/0 \
            -e SECRET_KEY=test-only -e ENVIRONMENT=test -e ALLOWED_HOSTS=test,localhost \
            -e DASHBOARD_SESSION_SECRET=test-only -e DASHBOARD_PASSWORD=test-only-dashboard-password \
            -e 'HMAC_KEYS={"test_app": "test-only-hmac-secret"}'

# luxarch:test-block asset v1 - DO NOT edit this marker line; it is how repo.emitted_assets_current knows your copy is current. Re-emit with `luxarch --emit test-block`.
# ── Test: THE suite, in the test image, against an isolated stack of real services ───────────────
# Emitted by `luxarch --emit test-block`; paste below the image block (it uses TEST_IMAGE and
# test-build from there). Enforced by `repo.test_block_wired`. Settings are `?=` defaults: set them
# above this block. What the suite can see, and what only `make smoke` sees: --doc
# FLEET-MAKEFILE-STANDARD §1.
#
# Before this block every repo wrote its own `make test`: four repos, four ways (a lint image with
# the source mounted, the dev image with pytest pip-installed at run time, a repo script, a test
# image), each with its own readiness loop and coverage wiring, and a coverage pipe under make's
# /bin/sh that hid pytest's failure. This is the documented practice of the tools instead:
#   - ISOLATED STACK (Docker Compose): the backing services run in a compose project of their own,
#     one per run (`-p`), so parallel runs never collide, the suite cannot reach the dev database or
#     cache at all (it is on another network), and teardown (`down --volumes --remove-orphans`)
#     removes exactly this run's containers and data, pass or fail, never the dev stack.
#   - READINESS from each service's own compose healthcheck (`up --wait`), not a sleep or a loop:
#     the service declares when it is ready. For Postgres, probe over TCP with the real role
#     (`pg_isready -h 127.0.0.1 -U <user> -d <db>`); over the socket it answers while initdb's
#     temporary server is still up. `repo.test_stack_parity` checks these are the services prod runs.
#   - Ctrl-C stops the suite (`--init` forwards the signal; a shell as PID 1 ignores it).
#   - THE TEST IMAGE built from this source (`test-build`, the image's `--with dev` stage), with the
#     fleet's pytest config (`luxlint --emit-config pytest`: -ra, strict markers and config,
#     warnings are errors), readable by the image's non-root user. The source is mounted read-only
#     and pytest writes no cache into it.
#   - COVERAGE with coverage.py itself, not pytest-cov: `coverage run --branch` under the sysmon
#     core (fast branch coverage on Python 3.14), data in /tmp, then `coverage report` judged by
#     `luxlint --coverage-ratchet` against `[test].coverage_min` (off until you set a floor; it
#     only ratchets up). `coverage` belongs in the dev dependency group.
#   - BOTH EXIT CODES reach make: pytest's and the ratchet's. No pipe carries either. The ratchet
#     reads coverage's own report file, never the suite's output (a printed `TOTAL … 100%` or
#     pytest's `[100%]` would otherwise pass for a measurement), and a report that measured nothing
#     fails.
#   - Each run's project is named from its own `mktemp -d` token, and refuses to run without one (a
#     PID repeats across containers and CI runners), and everything mounts the makefile's directory ($(CURDIR)), so `make -C` runs the
#     right suite.

# Setting: the compose command, and the profile holding the test services --------------------
TEST_COMPOSE ?= docker compose
TEST_PROFILE ?= test
# Setting: the env file compose interpolates the file with (it reads EVERY service, so an app
# service's `${TAG:?}` needs a value even when only the test services start) -----------------
TEST_ENV_FILE ?= $(firstword $(wildcard .env.test .env.dev .env.example))
# Setting: the backing services the suite needs (each with a healthcheck); empty: none -----------
TEST_SERVICES ?= db-test
# Setting: the suite's environment: the test services' URLs, by service name on the test network -
TEST_ENV ?= -e TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@db-test:5432/postgres
# Setting: where the suite runs from (a monorepo's apps/backend), and every package coverage
# measures, comma-separated (`app,collector`): it replaces any [tool.coverage.run] source ---------
TEST_WORKDIR ?= .
TEST_COV ?= app
# Setting: NONE. For a one-off run only, on the command line: `make test PYTEST_ARGS='-k orders'`.
# A committed value narrows THE suite for everyone (`repo.test_block_wired` reds one) ---------
PYTEST_ARGS ?=

# One-off pytest arguments reach the container through the environment, never spliced into a quoted
# command line (`-k 'a or b'` would otherwise split it).
export PYTEST_ARGS

test: test-build ## THE suite: test image, an isolated stack of real services, coverage ratchet
	@set -u; \
	D=$$(mktemp -d); tok=$$(basename "$$D" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9'); \
	[ $${#tok} -ge 8 ] || { echo "REFUSING: could not make a unique name for this run"; rm -rf "$$D"; exit 2; }; \
	run="t$$(printf '%s' '$(notdir $(CURDIR))' | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9_-' '-')-test-$$tok"; \
	dc="$(TEST_COMPOSE) -p $$run $(if $(TEST_ENV_FILE),--env-file $(TEST_ENV_FILE)) --profile $(TEST_PROFILE)"; \
	chmod 777 "$$D"; \
	trap '$$dc down --volumes --remove-orphans >/dev/null 2>&1; rm -rf "$$D"' EXIT INT TERM; \
	net=; if [ -n "$(TEST_SERVICES)" ]; then \
	  $$dc up -d --wait --wait-timeout 120 $(TEST_SERVICES) \
	    || { echo "FAIL  the test services did not become healthy: $(TEST_SERVICES)"; exit 1; }; \
	  cid=$$($$dc ps -q $(firstword $(TEST_SERVICES))); net=; \
	  for n in $$(docker inspect -f '{{range $$k, $$v := .NetworkSettings.Networks}}{{$$k}} {{end}}' $$cid); do \
	    [ "$$(docker network inspect -f '{{index .Labels "com.docker.compose.project"}}' $$n)" = "$$run" ] && { net=$$n; break; }; done; \
	  [ -n "$$net" ] || { echo "FAIL  $(firstword $(TEST_SERVICES)) joined no network of this run's own project ($$run)"; exit 1; }; \
	  net="--network $$net"; fi; \
	docker run --rm -v $(CURDIR):/repo $(LUXLINT) --emit-config pytest > "$$D/pytest.ini" || exit 2; \
	chmod 644 "$$D/pytest.ini"; \
	docker run --rm --init $$net $(TEST_ENV) -e PYTEST_ADDOPTS="$${PYTEST_ARGS:-}" \
	  -e COVERAGE_CORE=sysmon -e COVERAGE_FILE=/out/.coverage -e PYTHONDONTWRITEBYTECODE=1 \
	  -v $(CURDIR):/repo:ro -v "$$D":/out -w /repo/$(TEST_WORKDIR) $(TEST_IMAGE) \
	  sh -c 'python -m coverage run --branch --source=$(TEST_COV) -m pytest -c /out/pytest.ini --rootdir=. -p no:cacheprovider; s=$$?; python -m coverage report --show-missing > /out/coverage.txt; echo $$? > /out/coverage.rc; cat /out/coverage.txt; exit $$s'; \
	rc=$$?; \
	[ "$$rc" = 0 ] || { echo "FAIL  the suite failed (exit $$rc)"; exit 1; }; \
	[ "$$(cat "$$D/coverage.rc" 2>/dev/null)" = 0 ] || { echo "FAIL  coverage measured nothing (coverage report: $$(tail -n 1 "$$D/coverage.txt" 2>/dev/null)): check TEST_COV names the package the suite imports"; exit 1; }; \
	docker run --rm -i -v $(CURDIR):/repo $(LUXLINT) --coverage-ratchet < "$$D/coverage.txt" > "$$D/ratchet.txt"; crc=$$?; \
	sed -n '/coverage ratchet/,$$p' "$$D/ratchet.txt"; \
	[ "$$crc" = 0 ] || { echo "FAIL  the coverage ratchet failed (its verdict is above): add tests, never lower the floor"; exit 1; }

# =============================================================================
# Development — the ONE compose.yml with .env.dev (compose never builds: make dev-deploy)
# =============================================================================
DEV_COMPOSE := docker compose --env-file .env.dev

network: ## Create the fleet's shared docker network (once per host)
	@docker network inspect luxardolabs >/dev/null 2>&1 || docker network create luxardolabs

up: network ## Start the dev stack (the TAG pinned in .env.dev)
	$(DEV_COMPOSE) up -d
	@echo "✅ LuxAnalytics is running at https://localhost:4000"

dev: network ## Start the dev stack in the foreground
	$(DEV_COMPOSE) up

down:
	$(DEV_COMPOSE) down

restart:
	$(DEV_COMPOSE) restart

logs:
	$(DEV_COMPOSE) logs -f

logs-app:
	$(DEV_COMPOSE) logs -f luxanalytics_app

logs-db:
	$(DEV_COMPOSE) logs -f luxanalytics_db

shell:
	$(DEV_COMPOSE) exec luxanalytics_app /bin/bash

shell-db:
	$(DEV_COMPOSE) exec luxanalytics_db psql -U luxanalytics -d luxanalytics

migrate:
	$(DEV_COMPOSE) exec luxanalytics_app alembic upgrade head

migrate-down:
	$(DEV_COMPOSE) exec luxanalytics_app alembic downgrade -1

# Autogenerate runs in the test image with the checkout mounted, so the revision lands in
# alembic/versions/ (the app container has no source mount). Against the dev DB.
migrate-create: test-build ## New alembic revision from the models (autogenerate — READ it)
	@read -p "Enter migration message: " msg; \
	docker run --rm --network luxardolabs --env-file .env.dev -v $(PWD):/app -w /app \
	  $(TEST_IMAGE) alembic revision --autogenerate -m "$$msg"

clean:
	$(DEV_COMPOSE) down -v
	rm -rf __pycache__ .pytest_cache
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true

stack-status: ## Container status + health of the local stack
	@echo "🔍 LuxAnalytics v$(BUILD_VERSION)"
	@$(DEV_COMPOSE) ps
	@echo ""
	@$(DEV_COMPOSE) exec -T luxanalytics_app curl -sf http://localhost:4000/health | python3 -m json.tool 2>/dev/null || echo "❌ API not responding"

health:
	@$(DEV_COMPOSE) exec -T luxanalytics_app curl -sf http://localhost:4000/health | python3 -m json.tool || echo "❌ Service not responding"

ps:
	$(DEV_COMPOSE) ps

backup:
	@mkdir -p backups
	@$(DEV_COMPOSE) exec -T luxanalytics_db pg_dump -U luxanalytics luxanalytics > backups/luxanalytics_$$(date +%Y%m%d_%H%M%S).sql
	@echo "✅ Database backed up to backups/"

restore:
	@if [ -z "$(FILE)" ]; then echo "Usage: make restore FILE=backups/luxanalytics_YYYYMMDD_HHMMSS.sql"; exit 1; fi
	@$(DEV_COMPOSE) exec -T luxanalytics_db psql -U luxanalytics -d luxanalytics < $(FILE)
	@echo "✅ Database restored from $(FILE)"

work: ## Publish this commit, pin it in .env.dev, restart, migrate, tail logs
	$(MAKE) dev-deploy
	$(MAKE) migrate
	$(MAKE) logs

seed-sdk-app: ## Dev only: ensure the Swift SDK's integration-test app exists and print its DSN (LUXANALYTI-73)
	@$(DEV_COMPOSE) exec -T luxanalytics_app python -m app.scripts.seed_sdk_app

quick:
	$(DEV_COMPOSE) restart luxanalytics_app
	$(DEV_COMPOSE) logs -f luxanalytics_app

# luxarch:css-watch asset v1 - DO NOT edit this marker line; it is how repo.emitted_assets_current knows your copy is current. Re-emit with `luxarch --emit css-watch`.
# Live stylesheet rebuilds for local development, with Node in a throwaway container: nothing is
# installed on the host and nothing runs in compose. The output it writes is gitignored; the image
# builds its own (luxarch --emit css-stage). See luxarch --doc FLEET-BUILD-DEPLOY-STANDARD.
CSS_NODE_IMAGE ?= node:24-slim

.PHONY: css-watch
css-watch: ## Recompile the stylesheet on change (Node in a throwaway container; output gitignored)
	docker run --rm -it -v "$(CURDIR)":/w -w /w $(CSS_NODE_IMAGE) \
	  sh -c 'npm ci --no-audit --no-fund && npm run build:css -- --watch'

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
# -f compose.yml: the node may still hold the retired compose.yaml, which compose would prefer.
PROD_COMPOSE := docker compose -f compose.yml --env-file .env.prod

prod-sync: ## Sync the stack (compose.yml, scripts/init.sql, .env.prod) to the prod node
	@echo "Pushing deploy config to production..."
	@# Streamed through both ssh hops: no staging file on any host.
	@tar -czf - compose.yml scripts/init.sql -C deploy/prod .env.prod | $(PROD_SSH) 'mkdir -p $(PROD_PATH) && tar -xzf - -C $(PROD_PATH)/'"
	@echo "✅ Deploy config pushed to $(PROD_PATH)"

# Prod runs a CUT release: the immutable :$(VERSION) that `make release` pushed, persisted as TAG= in
# .env.prod so the stack comes back after a reboot. PROD_TAG=<older version> is the rollback path.
PROD_ENV := deploy/prod/.env.prod
PROD_TAG ?= $(VERSION)

prod-pin: ## Point .env.prod at an ALREADY-RELEASED version (PROD_TAG, default VERSION)
	@docker manifest inspect $(IMAGE):$(PROD_TAG) >/dev/null 2>&1 || \
	  { echo "$(IMAGE):$(PROD_TAG) is not in the registry — make release first"; exit 1; }
	@$(call pin_env_tag,$(PROD_ENV),$(PROD_TAG))

prod-deploy: prod-pin prod-sync ## Pin TAG, sync deploy/prod, pull + restart on the prod node
	@echo "Deploying LuxAnalytics $(PROD_TAG) to production..."
	@$(PROD_SSH) 'cd $(PROD_PATH) && $(PROD_COMPOSE) pull && $(PROD_COMPOSE) up -d'"
	@echo "✅ Deployed $(PROD_TAG)"

prod-restart:
	@echo "Restarting LuxAnalytics on production..."
	@$(PROD_SSH) 'cd $(PROD_PATH) && $(PROD_COMPOSE) restart luxanalytics_app'"
	@echo "✅ Restarted"

prod-stop:
	@echo "Stopping LuxAnalytics on production..."
	@$(PROD_SSH) 'cd $(PROD_PATH) && $(PROD_COMPOSE) down'"
	@echo "✅ Stopped"

prod-logs:
	@$(PROD_SSH) 'cd $(PROD_PATH) && $(PROD_COMPOSE) logs --tail 50 luxanalytics_app'"

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

prod-release: release prod-deploy ## Cut VERSION and deploy it to production
	@echo "✅ Released $(VERSION) to production"
