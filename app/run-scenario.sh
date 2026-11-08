#!/usr/bin/env bash
set -uo pipefail

SCENARIO="${1:-world/scenarios/election_at_dawn_8.json}"
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

if [ ! -f "$SCENARIO" ]; then
    echo "   REFUSED: no scenario file at '$SCENARIO'."
    echo "   Available: $(ls world/scenarios/*.json 2>/dev/null | tr '\n' ' ')"
    exit 2
fi

FACTS=$("$PY" - "$SCENARIO" <<'PYEOF'
import shlex, sys
from world.scenario import Scenario

s = Scenario.load(sys.argv[1])
r = s.recording
out = [
    ("SC_NAME", s.name),
    ("SC_GENESIS", str(s.genesis_path() or "")),
    ("SC_START", str(s.world.start_tick)),
    ("SC_STOP", str(s.world.stop_tick or "")),
    ("SC_CAP", "" if s.world.budget_cap_usd is None else f"{s.world.budget_cap_usd:.2f}"),
    ("SC_OUT", str(s.report.get("out", ""))),
    ("SC_URL", str(r.get("url", "http://127.0.0.1:8400/township-v2.html?book=election&roofs=off&camera=government:2.2"))),
    ("SC_INTERVAL", str(r.get("interval_s", 2))),
    ("SC_FPS", str(r.get("fps", 30))),
    ("SC_WIDTH", str(r.get("width", 1920))),
    ("SC_HEIGHT", str(r.get("height", 1080))),
    # One recorder per view. `recording.views` is a list of
    # {"name": ..., "url": ...}; with none given this is the single view
    # `recording.url` names, so every existing scenario records as before.
    ("SC_VIEWS", "\n".join(
        f"{v['name']}\t{v['url']}" for v in (r.get("views") or [])
    )),
    ("SC_COUNT_TICK", str(s.election_phase_ticks()[3])),
    ("SC_SAYS", "\n".join(s.describe())),
]
print("\n".join(f"{k}={shlex.quote(v)}" for k, v in out))
PYEOF
) || { echo "   REFUSED: $SCENARIO does not describe a run that could happen (see above)."; exit 2; }
eval "$FACTS"

NAME="${2:-$SC_NAME-$(date +%Y%m%d)}"
MAX_HOURS="${3:-6}"
CAP="${CAP:-${SC_CAP:-20}}"

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
BROKER_CAP=$("$PY" -c "print(float('$CAP') + 5)")

PIDS=()
cleanup() {
    for pid in "${PIDS[@]:-}"; do
        [ -n "$pid" ] && kill "$pid" 2>/dev/null
    done
    rmdir "$LOCK" 2>/dev/null
}
trap cleanup EXIT INT TERM

mkdir -p "$OUT" "$HOME_ROOT" data
RESUME_FLAG="--no-resume"
if [ "${RESUME:-}" = "1" ]; then
    if [ ! -e "$DB" ]; then
        echo "   REFUSED: RESUME=1 but there is no run called '$NAME' to continue."
        echo "   ($DB does not exist - a new run here would start the budget again.)"
        echo "   Runs you can continue:"
        ls -1 data/*.db 2>/dev/null | sed 's|data/||; s|\.db$||; s|^|     |' || echo "     (none)"
        echo "   Pass the name as the second argument, e.g."
        echo "     RESUME=1 $0 $SCENARIO <name-from-the-list> ${MAX_HOURS}"
        exit 2
    fi
    RESUME_FLAG=""
    echo "   resuming $NAME from $CP"
elif [ -e "$DB" ]; then
    echo "   REFUSED: $DB already exists - pick a new name, or RESUME=1 to continue it;"
    echo "   no run evidence is deleted (SG7)."
    exit 2
fi

echo "== $NAME, from $SCENARIO"
echo "$SC_SAYS" | sed 's/^/   /'
echo "   cap \$$CAP (broker \$$BROKER_CAP), ticks $SC_START-${SC_STOP:-until told}, up to ${MAX_HOURS}h, out $OUT"
printf '%s\n' "$SC_SAYS" > "$OUT/scenario.txt"
cp "$SCENARIO" "$OUT/scenario.json"

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
  --scenario "$SCENARIO" --checkpoint-dir "$CP" --world-mode embedded \
  --world-host 127.0.0.1 --world-port 8100 \
  --broker-base-url http://127.0.0.1:8200 \
  --supervisor-backend process --engine-repo ../engine $RESUME_FLAG \
  --resource-log "$OUT/resources.jsonl" \
  --budget-cap-cents "$CENTS" > "$OUT/runner.log" 2>&1 &
RUNNER=$!
PIDS+=($RUNNER)

TRACE_SQLITE_PATH="$DB" WORLD_BASE_URL="http://127.0.0.1:8100" \
  nohup "$PY" -m uvicorn observatory.app:app \
  --host 127.0.0.1 --port 8500 > "$OUT/observatory.log" 2>&1 &
PIDS+=($!)
WORLD_BASE_URL="http://127.0.0.1:8100" WORLD_WS_URL="ws://127.0.0.1:8100/ws/events" \
  RUN_BUDGET_USD="$CAP" \
  nohup "$PY" -m uvicorn game.server:app --host 127.0.0.1 --port 8400 > "$OUT/game.log" 2>&1 &
PIDS+=($!)
for _ in $(seq 1 60); do
    curl -sf -o /dev/null "http://127.0.0.1:8100/world/state" && \
    curl -sf -o /dev/null "http://127.0.0.1:8400/health" && break
    sleep 5
done
if [ -n "$SC_VIEWS" ] && [ "${NO_VIDEO:-}" != "1" ]; then
    MISSING=$("$PY" - <<'PROVEEOF'
import sys
sys.path.insert(0, "scripts")
from record_ui import find_chrome, find_ffmpeg
from pathlib import Path
missing = []
chrome = find_chrome()
if not (Path(chrome).is_absolute() and Path(chrome).exists()):
    missing.append("chrome")
if find_ffmpeg() is None:
    missing.append("ffmpeg")
print(" ".join(missing))
PROVEEOF
)
    if [ -n "$MISSING" ]; then
        echo "   REFUSED: the scenario asks for video and this machine cannot record it."
        echo "   Missing:$MISSING"
        echo
        echo "   Neither fix needs root:"
        case "$MISSING" in *chrome*)
            echo "     uv pip install --python .venv/bin/python playwright"
            echo "     .venv/bin/python -m playwright install chromium" ;;
        esac
        case "$MISSING" in *ffmpeg*)
            echo "     uv pip install --python .venv/bin/python imageio-ffmpeg" ;;
        esac
        echo
        echo "   Or run without video: NO_VIDEO=1 $0 $*"
        exit 2
    fi
fi
RECORDERS=""
VIEW_NAMES=""
PORT=9600
if [ "${NO_VIDEO:-}" = "1" ]; then
    echo "   recording DISABLED (NO_VIDEO=1) - trace, report and sessions still written"
elif [ -n "$SC_VIEWS" ]; then
    while IFS="$(printf '\t')" read -r VNAME VURL; do
        [ -n "$VNAME" ] || continue
        mkdir -p "$OUT/video/$VNAME"
        "$PY" -u scripts/record_ui.py --out "$OUT/video/$VNAME" ${CHROME:+--chrome "$CHROME"} \
          --url "$VURL" \
          --interval "$SC_INTERVAL" --fps "$SC_FPS" --width "$SC_WIDTH" --height "$SC_HEIGHT" \
          --port "$PORT" \
          --watch-url "http://127.0.0.1:8100/world/state" --watch-misses 120 \
          > "$OUT/record-$VNAME.log" 2>&1 &
        RECORDERS="$RECORDERS $!"
        VIEW_NAMES="$VIEW_NAMES $VNAME"
        PORT=$((PORT + 1))
    done <<VIEWSEOF
$SC_VIEWS
VIEWSEOF
    echo "   recording views:$VIEW_NAMES"
else
    "$PY" -u scripts/record_ui.py --out "$OUT/video" ${CHROME:+--chrome "$CHROME"} \
      --url "$SC_URL" \
      --interval "$SC_INTERVAL" --fps "$SC_FPS" --width "$SC_WIDTH" --height "$SC_HEIGHT" \
      --watch-url "http://127.0.0.1:8100/world/state" \
      > "$OUT/record.log" 2>&1 &
    RECORDERS="$!"
fi
echo "   runner pid $RUNNER; watch $SC_URL"
echo "   the vote is counted at tick $SC_COUNT_TICK; recording -> $OUT/video; logs in $OUT"

REPORT_EVERY="${REPORT_EVERY:-120}"
(
    while true; do
        sleep "$REPORT_EVERY"
        if "$PY" -m world.run_report --db "$DB" --home-root "$HOME_ROOT" \
             --out "$OUT/.report.next" >> "$OUT/report-live.log" 2>&1; then
            mv -f "$OUT/.report.next" "$OUT/report.md"
        fi
    done
) &
LIVE_REPORT=$!
echo "   live report -> $OUT/report.md, rewritten every ${REPORT_EVERY}s"

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

kill "$LIVE_REPORT" 2>/dev/null
wait "$LIVE_REPORT" 2>/dev/null
rm -f "$OUT/.report.next"

for R in $RECORDERS; do kill -INT "$R" 2>/dev/null; done
for R in $RECORDERS; do wait "$R" 2>/dev/null; done
mkdir -p "$OUT/sessions"
SESSIONS=0
for home in "$HOME_ROOT"/*/; do
    log="$home/session_log.jsonl"
    [ -f "$log" ] || continue
    cp "$log" "$OUT/sessions/$(basename "$home").jsonl" && SESSIONS=$((SESSIONS + 1))
done
echo "   kept $SESSIONS per-turn session file(s) in $OUT/sessions"

echo "== $NAME finished; video(s) under $OUT/video; writing the report"
"$PY" -m world.run_report --db "$DB" --home-root "$HOME_ROOT" --out "$OUT/report.md" \
  > "$OUT/report.log" 2>&1 || echo "   report failed - see $OUT/report.log"
echo "   trace $DB; checkpoints $CP; homes archived in $CP/engine-homes.tar.gz"
