#!/usr/bin/env bash
# Fifty residents, with what each one costs the machine written down.
#
#     ./run50.sh [name] [cap-usd] [minutes]
#
# WHAT IS DIFFERENT FROM runseries.sh. Two things, and both are the point:
# fifty engines instead of ten, and `--resource-log`, which writes one JSON
# line per engine per tick carrying rss_mb, peak_rss_mb, cpu_percent and
# whether it was alive.
#
# WHY FIFTY IS EVEN POSSIBLE. Preflight refused it until today, on
# arithmetic it had never checked here: MB_PER_ENGINE_PROCESS is 550, taken
# on a Linux box under load, and `measured_mb_per_engine` shells out to
# `ps`, which Windows does not have - so it returned None and the constant
# stood in for a measurement on every run this machine has ever made.
# Measured once the Windows branch existed: 42 MB an engine. Fifty of those
# is about 2 GB against 12 GB free, and check(50) now returns fits=True on
# evidence.
#
# The resource log is what turns that from a claim into a record. If the
# engines DO grow - the working-memory flush reached 99,591 tokens in a
# single call, so something accumulates - this is the file that shows it,
# per agent, over the whole run.
set -uo pipefail

NAME="${1:-fifty}"
CAP="${2:-15}"
MINUTES="${3:-40}"
GENESIS="world/genesis_50_town.json"

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

# Same lock runseries.sh holds. Four series once ran at once, deleting each
# other's directories and serving each other's calls into each other's
# stores; a fifty-engine run colliding with anything would be worse.
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

# 20s a tick, not the 15 the ten-resident series uses.
#
# Fifty residents on a three-tick cadence is about seventeen agents due per
# tick, and a turn averages three model calls - so roughly fifty calls a
# tick. At 15s that is 200 calls a minute, which is exactly the aggregate
# the broker serves (four of six lanes have no inference profile on this
# account, so it is 200 RPM and not 600). Running the town at precisely the
# rate the broker can serve leaves no room for a retry, and the runner's
# own pacing check warns about this in as many words.
KASUKABE_HOME_ROOT="$HOME_ROOT" TRACE_SQLITE_PATH="$DB" SPEND_CAP_USD="$CAP" \
  nohup .venv/Scripts/python.exe -m world.run_genesis \
  --genesis-path "$GENESIS" --world-mode embedded \
  --world-host 127.0.0.1 --world-port 8100 \
  --broker-base-url http://127.0.0.1:8200 \
  --supervisor-backend process --engine-repo ../engine --no-resume \
  --start-at-dawn --resource-log "$RESOURCES" \
  --tick-interval-s 20 --budget-cap-cents "$CENTS" > "$OUT/runner.log" 2>&1 &

# UI-11: the observatory, pointed at THIS RUN's trace store.
#
# It was never started by any run script - not here, not run50.sh, not
# start-genesis.sh - and the only launcher was docker-compose, whose
# `observatory` service reads `/data/run.db` while this harness writes
# `data/$NAME.db`. So the one instrument for watching a run in flight was
# either not running at all (run 23: "not reachable on :8500") or running
# against a file no run had ever written.
#
# Started here because this is where the trace path is known. Best-effort:
# a run must not fail because its observatory did not come up, and a run
# WITH no observatory is exactly the state this fixes, so it is announced
# rather than swallowed.
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

# Fifty engines take longer to boot than ten, so the quiet window is wider
# than runseries.sh's 300s. It must stay a MULTIPLE of the longest a tick
# may legitimately take (tick_timeout_s is 120), never equal to it - a
# window equal to the timeout once killed five healthy runs at forty
# seconds each and graded them.
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
