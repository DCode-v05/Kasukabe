#!/usr/bin/env bash
set -uo pipefail

SIZE="${1:-20}"
case "$SIZE" in
    20) DEFAULT_CAP=280 ;;
    50) DEFAULT_CAP=650 ;;
    *) echo "   REFUSED: residents must be 20 or 50, not '$SIZE'."; exit 2 ;;
esac
NAME="${2:-election$SIZE}"
CAP="${3:-$DEFAULT_CAP}"
MAX_HOURS="${4:-12}"
GENESIS="world/genesis_${SIZE}_funded.json"
STOP_TICK="${STOP_TICK:-590}"

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"
PY="${PY:-}"
if [ -z "$PY" ]; then
    for candidate in .venv/bin/python .venv/Scripts/python.exe; do
        [ -x "$candidate" ] && PY="$candidate" && break
    done
fi
[ -n "$PY" ] || PY="$(command -v python3 || command -v python)"
if [ -z "$PY" ]; then
    echo "   REFUSED: no python found. Looked for \$PY, .venv/bin/python,"
    echo "   .venv/Scripts/python.exe, then python3 and python on PATH."
    exit 2
fi

LOCK="/tmp/kasukabe-runseries.lock"
if ! mkdir "$LOCK" 2>/dev/null; then
    echo "   REFUSED: another run holds $LOCK. One at a time."
    exit 2
fi

OUT="${OUT:-../docs/runs/series/$NAME}"
HOME_ROOT="$HOME/kasukabe-homes/$NAME"   # not /tmp: macOS clears it (SG7)
DB="data/$NAME.db"
CP="data/checkpoints-$NAME"
CENTS=$("$PY" -c "print(int(float('$CAP') * 100))")
BROKER_CAP=$("$PY" -c "print(float('$CAP') + 10)")

PIDS=()
cleanup() {
    for pid in "${PIDS[@]:-}"; do
        [ -n "$pid" ] && kill "$pid" 2>/dev/null
    done
    rmdir "$LOCK" 2>/dev/null
}
trap cleanup EXIT INT TERM

mkdir -p "$OUT" "$HOME_ROOT"
RESUME_FLAG="--no-resume"
if [ "${RESUME:-}" = "1" ]; then
    RESUME_FLAG=""
    echo "   resuming $NAME from $CP"
elif [ -e "$DB" ]; then
    echo "   REFUSED: $DB already exists - pick a new name, or RESUME=1 to continue it;"
    echo "   no run evidence is deleted (SG7)."
    exit 2
fi

echo "== $NAME: $SIZE residents, cap \$$CAP (broker \$$BROKER_CAP), to tick $STOP_TICK, up to ${MAX_HOURS}h, ${GENESIS}"

TRACE_SQLITE_PATH="$DB" SPEND_CAP_USD="$BROKER_CAP" \
  nohup "$PY" -m uvicorn broker.app:app \
  --host 127.0.0.1 --port 8200 > "$OUT/broker.log" 2>&1 &
PIDS+=($!)

for _ in $(seq 1 40); do
    curl -sf -o /dev/null "http://127.0.0.1:8200/health" && break
    sleep 2
done
if ! curl -sf -o /dev/null "http://127.0.0.1:8200/health"; then
    echo "   VOID: the broker never came up - see $OUT/broker.log" | tee "$OUT/VOID.txt"
    exit 1
fi
grep -h "will NOT be routed to" "$OUT/broker.log" && echo "   (running without the overflow lane)"

KASUKABE_HOME_ROOT="$HOME_ROOT" TRACE_SQLITE_PATH="$DB" SPEND_CAP_USD="$BROKER_CAP" \
  nohup "$PY" -m world.run_genesis \
  --genesis-path "$GENESIS" --checkpoint-dir "$CP" --world-mode embedded \
  --world-host 127.0.0.1 --world-port 8100 \
  --broker-base-url http://127.0.0.1:8200 \
  --supervisor-backend process --engine-repo ../engine $RESUME_FLAG \
  --start-at-dawn --max-ticks "$STOP_TICK" --max-game-days 5 --resource-log "$OUT/resources.jsonl" \
  --budget-cap-cents "$CENTS" > "$OUT/runner.log" 2>&1 &
RUNNER=$!
PIDS+=($RUNNER)

TRACE_SQLITE_PATH="$DB" WORLD_BASE_URL="http://127.0.0.1:8100" \
  nohup "$PY" -m uvicorn observatory.app:app \
  --host 127.0.0.1 --port 8500 > "$OUT/observatory.log" 2>&1 &
PIDS+=($!)
WORLD_BASE_URL="http://127.0.0.1:8100" WORLD_WS_URL="ws://127.0.0.1:8100/ws/events" \
  nohup "$PY" -m uvicorn game.server:app --host 127.0.0.1 --port 8400 > "$OUT/game.log" 2>&1 &
PIDS+=($!)
for _ in $(seq 1 60); do
    curl -sf -o /dev/null "http://127.0.0.1:8100/world/state" && \
    curl -sf -o /dev/null "http://127.0.0.1:8400/health" && break
    sleep 5
done
"$PY" -u scripts/record_ui.py --out "$OUT/video" ${CHROME:+--chrome "$CHROME"} \
  --url "http://127.0.0.1:8400/township-v2.html?book=election&roofs=off&camera=government:2.2" \
  --interval 2 --fps 30 --width 1920 --height 1080 \
  --watch-url "http://127.0.0.1:8100/world/state" \
  > "$OUT/record.log" 2>&1 &
RECORDER=$!
echo "   runner pid $RUNNER; viewer http://127.0.0.1:8400/township-v2.html?book=election&roofs=off;"
echo "   observatory http://127.0.0.1:8500; recording -> $OUT/video; logs in $OUT"

QUIET_S=600
DEADLINE=$(( $(date +%s) + MAX_HOURS * 3600 ))
LAST_SIZE=0
LAST_CHANGE=$(date +%s)
while kill -0 "$RUNNER" 2>/dev/null && [ "$(date +%s)" -lt "$DEADLINE" ]; do
    sleep 30
    SIZE=$(wc -c < "$OUT/runner.log" 2>/dev/null || echo 0)
    if [ "$SIZE" != "$LAST_SIZE" ]; then
        LAST_SIZE="$SIZE"; LAST_CHANGE=$(date +%s)
    elif [ $(( $(date +%s) - LAST_CHANGE )) -ge "$QUIET_S" ]; then
        echo "   runner log quiet for ${QUIET_S}s - stopping"
        kill -INT "$RUNNER" 2>/dev/null
        sleep 60
        break
    fi
done

kill -INT "$RECORDER" 2>/dev/null
wait "$RECORDER" 2>/dev/null
mkdir -p "$OUT/sessions"
SESSIONS=0
for home in "$HOME_ROOT"/*/; do
    log="$home/session_log.jsonl"
    [ -f "$log" ] || continue
    cp "$log" "$OUT/sessions/$(basename "$home").jsonl" && SESSIONS=$((SESSIONS + 1))
done
echo "   kept $SESSIONS per-turn session file(s) in $OUT/sessions"
[ "$SESSIONS" -gt 0 ] || echo "   WARNING: no session files kept - this run cannot be re-audited"

echo "== $NAME finished; video $OUT/video/run.mp4; writing the report"
"$PY" -m world.run_report --db "$DB" --home-root "$HOME_ROOT" --out "$OUT/report.md" \
  > "$OUT/report.log" 2>&1 || echo "   report failed - see $OUT/report.log"
echo "   trace $DB; checkpoints $CP; homes archived in $CP/engine-homes.tar.gz"
