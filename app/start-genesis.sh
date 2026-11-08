#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")"

DATA=/tmp/kasukabe-data
mkdir -p "$DATA/checkpoints"

BUDGET_CAP_CENTS=${BUDGET_CAP_CENTS:-38000}

AUTH_SHARED_SECRET=${AUTH_SHARED_SECRET:-$(python -c "import secrets;print(secrets.token_hex(24))")}
OPERATOR_TOKEN=${OPERATOR_TOKEN:-$(python -c "import secrets;print(secrets.token_hex(24))")}
echo "operator token for this run: $OPERATOR_TOKEN"
echo "  (export OPERATOR_TOKEN and AUTH_SHARED_SECRET to reuse them)"

docker compose run --rm --no-deps -d \
  -p 8200:8200 \
  -e MOCK_MODE=false \
  -e WORLD_BASE_URL=http://host.docker.internal:8100 \
  -e TRACE_SQLITE_PATH=/data/kasukabe.db \
  -e AWS_REGION=ap-south-1 \
  -e AWS_SHARED_CREDENTIALS_FILE=/aws/credentials \
  -e AWS_CONFIG_FILE=/aws/config \
  -e AUTH_SHARED_SECRET="$AUTH_SHARED_SECRET" \
  -e SPEND_CAP_USD=$((BUDGET_CAP_CENTS / 100)) \
  -v "$HOME/.aws:/aws:ro" \
  -v "$DATA:/data" \
  broker python -m uvicorn broker.app:app --host 0.0.0.0 --port 8200

echo "broker starting on :8200 (6 lanes, cap \$$((BUDGET_CAP_CENTS / 100)))"
sleep 20

docker compose run --rm --no-deps -d \
  -e MOCK_MODE=false \
  -e OPERATOR_TOKEN="$OPERATOR_TOKEN" \
  -e AUTH_SHARED_SECRET="$AUTH_SHARED_SECRET" \
  -e TRACE_SQLITE_PATH=/data/kasukabe.db \
  -v "$DATA:/data" \
  -v "$(cd .. && pwd)/engine:/engine:ro" \
  -v "$(cd .. && pwd)/smallville:/smallville:ro" \
  world python -m world.run_genesis \
    --live \
    --world-mode embedded \
    --broker-base-url http://host.docker.internal:8200 \
    --checkpoint-dir /data/checkpoints \
    --budget-cap-cents "$BUDGET_CAP_CENTS" \
    --retrieval-depth 1.0 \
    --supervisor-backend process \
    --engine-repo /engine

echo "genesis launched"
