# Engine — Container Image
#
# Multi-stage build:
#   - base:    shared system deps (python, bubblewrap, gcc)
#   - prod:    runtime deps only, src/ + migrations/, runs uvicorn (default target)
#   - test:    runtime + dev deps, src/ + tests/, runs pytest
#
# Build prod: docker build -t engine .
# Build test: docker build --target test -t engine-test .
# Run prod:   docker run --env-file .env -v catalog:/catalog -v data:/data engine
# Run tests:  docker compose run --rm test           (uses bind-mounted source)

# ── base ────────────────────────────────────────────────────────────
FROM python:3.13-slim AS base

WORKDIR /app

# bubblewrap only. gcc is deliberately absent: every dependency resolves to a
# prebuilt wheel (verified with `pip install --only-binary=:all:`), nothing in
# src/ shells out to a compiler, and the sandbox allowlist never exposes one.
# Carrying it cost 166 MB. If a future dependency needs to build from source,
# add it to a separate build stage rather than to the runtime image.
RUN apt-get update && apt-get install -y --no-install-recommends \
    bubblewrap \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .

# ── prod ────────────────────────────────────────────────────────────
FROM base AS prod

RUN pip install --no-cache-dir .

COPY src/ src/
COPY migrations/ migrations/
COPY scripts/ scripts/

RUN groupadd -g 1000 enginegroup && \
    useradd -u 1000 -g enginegroup -m -s /bin/false engineuser && \
    mkdir -p /data /catalog && \
    chown -R engineuser:enginegroup /data /catalog /app /home/engineuser

# The engine root. Must point at a WRITABLE path: the prod rootfs is read-only
# (docker-compose.yml `read_only: true`), so leaving this unset would resolve to
# ~/.september on the immutable rootfs and fail at boot. /data is the durable
# volume declared below, already created and chowned to engineuser above.
#
# The mount must also allow exec: bubblewrap writes a shell script into
# $SEPTEMBER_HOME/workspaces and execve()s it, and a noexec mount turns every
# bash call into a bare "Permission denied".
ENV SEPTEMBER_HOME=/data

VOLUME /data
VOLUME /catalog

USER engineuser

EXPOSE 8000

CMD ["uvicorn", "src.server:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]

# ── test ────────────────────────────────────────────────────────────
# Used by `docker compose run --rm test`. Source is bind-mounted at runtime,
# so the COPY here exists only so the image can be built and run standalone.
FROM base AS test

RUN pip install --no-cache-dir ".[dev]"

COPY src/ src/
COPY tests/ tests/
COPY migrations/ migrations/
COPY catalog/ catalog/
COPY evals/ evals/
COPY scripts/ scripts/
# The env-var documentation contract: tests/test_config.py asserts every
# Settings env var appears in .env.example, keeping the doc in lockstep.
COPY .env.example .env.example

ENV PYTHONPATH=/app
ENV PYTHONDONTWRITEBYTECODE=1

CMD ["pytest", "tests/", "-x", "--tb=short"]
