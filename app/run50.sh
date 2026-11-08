#!/usr/bin/env bash
set -uo pipefail

NAME="${1:-fifty}"
CAP="${2:-15}"
MINUTES="${3:-40}"
GENESIS="world/genesis_50_town.json"

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

LOCK="/tmp/kasukabe-runseries.lock"
if ! mkdir "$LOCK" 2>/dev/null; then
    echo "   REFUSED: another run holds $LOCK. One at a time."
    exit 2
fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

OUT="../docs/runs/series/$NAME"
HOME_ROOT="/tmp/k_$NAME"
DB="data/$NAME.db"
RESOURCES="$OUT/resources.jsonl"
CENTS=$(python -c "print(int(float('$CAP') * 100))")

mkdir -p "$OUT"
rm -f "$DB"*; rm -rf "$HOME_ROOT"; mkdir -p "$HOME_ROOT"

echo "== $NAME: 50 residents, cap \$$CAP, up to ${MINUTES}m"
echo "   per-agent CPU and memory -> $RESOURCES"

taskkill //F //IM python.exe //T >/dev/null 2>&1 || true
sleep 12

TRACE_SQLITE_PATH="$DB" SPEND_CAP_USD="$CAP" \
  nohup .venv/Scripts/python.exe -m uvicorn broker.app:app \
  --host 127.0.0.1 --port 8200 > "$OUT/broker.log" 2>&1 &

for _ in $(seq 1 40); do
    curl -sf -o /dev/null "http://127.0.0.1:8200/health" && break
    sleep 2
done
if ! curl -sf -o /dev/null "http://127.0.0.1:8200/health"; then
    echo "   VOID: the broker never came up." | tee "$OUT/VOID.txt"
    exit 1
fi

KASUKABE_HOME_ROOT="$HOME_ROOT" TRACE_SQLITE_PATH="$DB" SPEND_CAP_USD="$CAP" \
  nohup .venv/Scripts/python.exe -m world.run_genesis \
  --genesis-path "$GENESIS" --world-mode embedded \
  --world-host 127.0.0.1 --world-port 8100 \
  --broker-base-url http://127.0.0.1:8200 \
  --supervisor-backend process --engine-repo ../engine --no-resume \
  --start-at-dawn --resource-log "$RESOURCES" \
  --tick-interval-s 20 --budget-cap-cents "$CENTS" > "$OUT/runner.log" 2>&1 &

TRACE_SQLITE_PATH="$DB" WORLD_BASE_URL="http://127.0.0.1:8100" \
  nohup .venv/Scripts/python.exe -m uvicorn observatory.app:app \
  --host 127.0.0.1 --port 8500 > "$OUT/observatory.log" 2>&1 &

for _ in $(seq 1 10); do
    curl -sf -o /dev/null "http://127.0.0.1:8500/api/health" && break
    sleep 1
done
if curl -sf -o /dev/null "http://127.0.0.1:8500/api/health"; then
    echo "   observatory: http://127.0.0.1:8500 (watching $DB)"
else
    echo "   observatory did NOT come up - this run has no live instrument." \
      | tee -a "$OUT/observatory.log"
fi

QUIET_S=420
DEADLINE=$(( $(date +%s) + MINUTES * 60 ))
LAST_SIZE=0
LAST_CHANGE=$(date +%s)
while [ "$(date +%s)" -lt "$DEADLINE" ]; do
    sleep 30
    SIZE=$(wc -c < "$OUT/runner.log" 2>/dev/null || echo 0)
    if [ "$SIZE" != "$LAST_SIZE" ]; then
        LAST_SIZE="$SIZE"
        LAST_CHANGE=$(date +%s)
    elif [ $(( $(date +%s) - LAST_CHANGE )) -ge "$QUIET_S" ]; then
        echo "   runner log quiet for ${QUIET_S}s - run has stopped"
        break
    fi
done

taskkill //F //IM python.exe //T >/dev/null 2>&1 || true
sleep 4

mkdir -p "$OUT/sessions"
for d in "$HOME_ROOT"/*/; do
    [ -f "$d/session_log.jsonl" ] && cp "$d/session_log.jsonl" "$OUT/sessions/$(basename "$d").jsonl"
done

CALLS=$(.venv/Scripts/python.exe -c "
import sqlite3
try:
    conn = sqlite3.connect('file:$DB?mode=ro', uri=True)
    print(conn.execute('select count(*) from llm_calls').fetchone()[0])
except Exception:
    print(0)
")
if [ "${CALLS:-0}" -lt 40 ]; then
    echo "   VOID: $NAME recorded ${CALLS:-0} calls. A run that did not happen is not a result." | tee "$OUT/VOID.txt"
    exit 1
fi

echo
.venv/Scripts/python.exe loop_report.py "$DB" | tee "$OUT/gates.txt"
echo
.venv/Scripts/python.exe -m world.evals.behavioural "$DB" | tee "$OUT/behavioural.txt"
echo
.venv/Scripts/python.exe -m world.resource_log "$RESOURCES" | tee "$OUT/resources.txt"
echo "   -> $OUT"
