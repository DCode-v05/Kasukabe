# Kasukabe app/ — Container Image
#
# Multi-stage build (mirrors ../engine/Dockerfile's shape):
#   - base: shared system deps + the one pyproject.toml for the whole
#     app/ monorepo. world, broker, game, research, and supabase all share
#     a single dependency set (see pyproject.toml) rather than one each,
#     so there is one base layer, not five.
#   - prod: runtime stage. Copies every package's source in and runs as a
#     non-root user. There is no per-service image: world and broker (the
#     two long-running processes this stage supports) come from the same
#     image, and docker-compose.yml's "world"/"broker" services pick which
#     one to run by overriding `command:`. One image is materially cheaper
#     to build and push than two nearly-identical ones for a monorepo this
#     size, and the two processes never disagree about dependency versions
#     by construction.
#   - test: runtime + full source, so the image is runnable standalone
#     (`docker run kasukabe-app-test`). docker-compose.yml's "test" service
#     bind-mounts the working tree over this at run time so host edits are
#     live without a rebuild; the COPY here only matters for a bare
#     `docker build --target test` with no compose involved.
#
# Build prod:  docker build -t kasukabe-app .
# Build test:  docker compose build test
# Run world:   docker compose up world
# Run broker:  docker compose up broker
# Run tests:   docker compose run --rm test        (uses bind-mounted source)

# ── base ────────────────────────────────────────────────────────────
FROM python:3.13-slim AS base

WORKDIR /app

# No compiler installed on purpose: fastapi/uvicorn[standard]/pydantic/
# boto3/httpx/websockets all resolve to prebuilt manylinux wheels on
# cpython 3.13 / linux x86_64 (the platform this image is pinned to, see
# docker-compose.yml). If a future dependency needs to build from source,
# add build-essential to a dedicated build stage rather than to base.
COPY pyproject.toml .
RUN pip install --no-cache-dir .

# The September engine's own runtime dependencies. This image does not
# just serve world/ and broker/: run_genesis.py's process supervisor
# spawns 250 engine subprocesses INSIDE this container, so the engine has
# to be importable here. Without these every agent dies at import
# (observed: ModuleNotFoundError: No module named 'aiosqlite') and the
# runner reports "tick dispatch failed" for all 250 while spend stays $0.
# Kept in sync with ../engine/pyproject.toml's dependencies; the ones the
# app already pulls in (pydantic, httpx, fastapi, uvicorn) are omitted.
RUN pip install --no-cache-dir \
    "aiosqlite~=0.20" \
    "sqlite-vec>=0.1.7" \
    "cryptography>=48.0.1,<49" \
    "jsonschema~=4.21" \
    "pyyaml~=6.0" \
    "Pillow>=12.3.0,<13" \
    "septemberai>=1.2.0,<2"

# research/parquet_export.py's DuckDB sqlite scanner extension is not
# bundled with the duckdb wheel; DuckDB downloads it on first INSTALL and
# caches it under $HOME/.duckdb/extensions. Pre-fetching it here, once, at
# build time (as the same root user both this stage and the "test" stage
# below run as - there is no USER switch until the "prod" stage) means
# `docker compose run --rm test` never needs network access to run
# research/tests/ - app/CONVENTIONS.md's "tests must be fast, free, and
# deterministic" extends to "do not depend on an extension CDN being
# reachable," the same reasoning that keeps every AWS call out of tests.
RUN python3 -c "import duckdb; duckdb.connect().execute('INSTALL sqlite')"

# ── prod ────────────────────────────────────────────────────────────
FROM base AS prod

COPY world/ world/
COPY broker/ broker/
COPY game/ game/
COPY research/ research/
COPY supabase/ supabase/
# Part C ships the observatory as a service on :8500. It was never
# copied in, so `uvicorn observatory.app:app` inside this image failed
# with ModuleNotFoundError and the page could only run from a host
# checkout or a bind mount.
COPY observatory/ observatory/
COPY tests/ tests/

RUN groupadd -g 1000 kasukabegroup && \
    useradd -u 1000 -g kasukabegroup -m -s /bin/false kasukabeuser && \
    chown -R kasukabeuser:kasukabegroup /app

ENV PYTHONPATH=/app
ENV PYTHONDONTWRITEBYTECODE=1

USER kasukabeuser

EXPOSE 8100 8200

# No default CMD: world and broker are different processes sharing this
# one image, and docker-compose.yml's "world"/"broker" services each set
# their own `command:` (uvicorn world.app:app / uvicorn broker.app:app).
# Running this image directly with no command is a deliberate no-op guard
# against accidentally starting the wrong service.
CMD ["python", "-c", "print('specify a command: uvicorn world.app:app --host 0.0.0.0 --port 8100, or uvicorn broker.app:app --host 0.0.0.0 --port 8200')"]

# ── test ────────────────────────────────────────────────────────────
# Used by `docker compose run --rm test`. Source is bind-mounted at run
# time (see docker-compose.yml), so the COPY here exists only so the image
# builds and runs standalone.
FROM base AS test

COPY world/ world/
COPY broker/ broker/
COPY game/ game/
COPY research/ research/
COPY supabase/ supabase/
# Part C ships the observatory as a service on :8500. It was never
# copied in, so `uvicorn observatory.app:app` inside this image failed
# with ModuleNotFoundError and the page could only run from a host
# checkout or a bind mount.
COPY observatory/ observatory/
COPY tests/ tests/

ENV PYTHONPATH=/app
ENV PYTHONDONTWRITEBYTECODE=1

# Bare `pytest`, not `pytest tests/`: unlike engine/, this monorepo keeps
# each package's tests inside that package's own tests/ directory
# (app/CONVENTIONS.md "Test conventions" - world/tests/, broker/tests/,
# world/map/tests/, and so on, plus app-wide invariant tests in the
# top-level tests/). A bare `pytest` run from /app matches `make test`
# (`uv run pytest`) and lets pytest's default rootdir discovery collect
# every one of those directories; `pytest tests/` would silently run only
# the two top-level cross-module tests and skip every package's own suite.
CMD ["pytest", "--tb=short"]
