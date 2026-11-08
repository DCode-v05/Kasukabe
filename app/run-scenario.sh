#!/usr/bin/env bash
# Run a scenario: the town, the tick it starts on, and the situation already
# in place when it does.
#
#     ./run-scenario.sh world/scenarios/election_at_dawn_8.json
#
# WHAT IS DIFFERENT FROM run-election.sh. That script hard-codes one run -
# twenty or fifty residents, dawn to tick 590, the default charter - and the
# election it is named for happens three game-days in, which is $129-163 of
# thinking before the question is asked. This one takes a file
# (world/scenario.py, world/scenarios/*.json) and obeys it: which cast, which
# tick to start on, which tick to stop at, how long the election's phases are,
# and who is already sitting in government when the first tick runs. The
# scenario is read and refused here before a cent is spent, and the runner
# reads the same file through --scenario.
#
# WHY A GOVERNMENT IS ALREADY SITTING. An election with vacant seats asks
# "will anyone stand?". An election with a government in it asks "will the
# town keep them?", which is the question DESIGN section 10's regime metrics
# are about, and it asks it of residents who have been governed by those
# people since the run began. The seats are marked `caretaker` - nobody voted
# for them - so no term is counted for them.
#
# WHAT IT STARTS, all bound to the one world on 127.0.0.1:8100: the broker
# (:8200), the runner with the embedded world, the town viewer (:8400), the
# observatory (:8500) and the camera. It stops them together, then writes the
# report - which opens with the checklist scored from the trace.
#
# ARGUMENTS: the scenario file, then optionally a run name (default: the
# scenario's name) and a wall-clock ceiling in hours (default 6). The cap
# comes from the scenario; CAP=... overrides it. Knobs: PY, OUT, CHROME,
# RESUME=1 (continue a cut run from its last checkpoint).
#
# PRECONDITIONS - the run refuses without them:
#   * the scenario loads. A stop tick before the start, an incumbent who is
#     not in the cast, an election whose windows are longer than the gap
#     between elections: all refused here, not forty minutes in.
set -uo pipefail

SCENARIO="${1:-world/scenarios/election_at_dawn_8.json}"
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"
# The interpreter, in order of preference: an explicit PY, this project's
# venv on either layout, then whatever python is on PATH.
#
# `.venv/bin/python` is the posix layout; a Windows venv puts it at
# `.venv/Scripts/python.exe`. With only the posix path tried and `python3`
# absent - which is normal on Windows, where the launcher is `python` -
# `PY` came out EMPTY and the script died on line 77 with
# `: command not found`, a message that names nothing and sends the reader
# looking at line 77 rather than here.
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

# One read of the file, in Python, into shell variables. A second parser in
# bash would be a second set of rules for the same file.
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

# Dated by default, so two runs of the same scenario cannot land on top of
# one another and every folder says when it was made. An explicit name still
# wins, which is what a RESUME must pass - see the guard below.
NAME="${2:-$SC_NAME-$(date +%Y%m%d)}"
MAX_HOURS="${3:-6}"
CAP="${CAP:-${SC_CAP:-20}}"

LOCK="/tmp/kasukabe-runseries.lock"
if ! mkdir "$LOCK" 2>/dev/null; then
    echo "   REFUSED: another run holds $LOCK. One at a time."
    exit 2
fi

# $NAME before the scenario's own `report.out`: a scenario that hardcodes a
# folder would otherwise send every run of it - including two on different
# days - into the same directory, which is exactly what dating the name is
# for. OUT= still overrides everything.
OUT="${OUT:-../docs/runs/series/$NAME}"
HOME_ROOT="$HOME/kasukabe-homes/$NAME"   # not /tmp: macOS clears it (SG7)
DB="data/$NAME.db"
CP="data/checkpoints-$NAME"
CENTS=$("$PY" -c "print(int(float('$CAP') * 100))")
# The broker's cap sits $5 above the runner's, so the end-of-run learning
# drain has room and the runner's governor is the one that stops the town.
BROKER_CAP=$("$PY" -c "print(float('$CAP') + 5)")

PIDS=()
cleanup() {
    for pid in "${PIDS[@]:-}"; do
        [ -n "$pid" ] && kill "$pid" 2>/dev/null
    done
    rmdir "$LOCK" 2>/dev/null
}
trap cleanup EXIT INT TERM

# `data/` too, and not only $OUT. It holds the trace store and the
# checkpoints, and it exists on a developer's machine because tracked files
# live in it - so nothing ever created it, and nothing noticed. A fresh
# machine that took the code as an archive has no empty directories at all:
# the broker died on "unable to open database file", the runner printed
# "VOID: the broker never came up", and the cause was one missing mkdir.
mkdir -p "$OUT" "$HOME_ROOT" data
RESUME_FLAG="--no-resume"
if [ "${RESUME:-}" = "1" ]; then
    # A RESUME that finds nothing to resume used to start a FRESH run, with a
    # fresh budget, while the operator believed they were continuing one. A
    # mistyped name was all it took, and dated names are longer and easier to
    # mistype. Worse, the box stops runs overnight: resuming the next morning
    # without naming the run computes TODAY's date, matches nothing, and would
    # have silently started the $60 again.
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

# --scenario carries the cast, the start and stop ticks, the charter's rules
# and the seats; the flags here are this machine's business, not the
# scenario's.
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
# RUN_BUDGET_USD is the cap the OPERATOR asked for. The broker's own ceiling
# sits $5 above it for the end-of-run drain, so without this the HUD would
# read "of $65.00" on a $60 run - a number nobody chose and which invites the
# operator to expect five dollars of run that is not coming.
WORLD_BASE_URL="http://127.0.0.1:8100" WORLD_WS_URL="ws://127.0.0.1:8100/ws/events" \
  RUN_BUDGET_USD="$CAP" \
  nohup "$PY" -m uvicorn game.server:app --host 127.0.0.1 --port 8400 > "$OUT/game.log" 2>&1 &
PIDS+=($!)
for _ in $(seq 1 60); do
    curl -sf -o /dev/null "http://127.0.0.1:8100/world/state" && \
    curl -sf -o /dev/null "http://127.0.0.1:8400/health" && break
    sleep 5
done
# One camera per view, all filming the same live world.
#
# A run has one truth and many faces: the map with its roofs on, the same
# map with them off and people visible indoors, the group chats, the vote,
# the money, the government. A single recording can show one of those, and
# afterwards nobody can ask the others - the world is gone. Each view is
# its own headless Chrome, so each needs a DevTools port of its own or the
# second one attaches to the first one's browser and films the same view
# twice. The profile directory needs no flag: `record_ui.py` already
# mkdtemps one per process.
#
# `--watch-misses 120` rather than the default 3. The watch exists so a
# camera stops when the world does, and three misses is a hair trigger
# once six Chromes are asking at once: on the first 20-resident run five
# of the six cameras decided the world had died, encoded what they had
# and quit, nine ticks in, while the world was answering in about a
# millisecond. 120 misses is still under five minutes of real silence.
#
# But first: prove there is something to film WITH. record_ui.py sends
# Chrome's stdout and stderr to DEVNULL, so a missing binary does not fail
# the run - it produces six empty view directories and a report nobody can
# watch, discovered hours later when the world is gone. Asked here, once,
# rather than six times in six background processes whose errors are
# discarded. Both fallbacks need no root, which is the whole point on a box
# with no sudo.
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

# A report that is only written at the end is a report nobody can read while
# the run is the thing they are watching - and on the team box, where the
# machine stops after four hours whether the run is finished or not, "at the
# end" may be hours away or may never arrive. So the same generator that
# writes the final report runs on a loop throughout.
#
# It is safe to point at a live trace: every connection run_report opens is
# `mode=ro`, so it cannot lock or disturb the database the run is writing.
# Measured at 2.1s against a 116 MB trace, which at REPORT_EVERY=120 is under
# 2% of one core.
#
# Written to a temporary file and moved into place, because `mv` within a
# directory is atomic: a reader refreshing report.md sees the previous report
# or the new one, never half of one being written. A failed pass keeps the
# last good report rather than truncating it.
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

# Stop the live report before anything else writes report.md, or the loop's
# next `mv` lands on top of the final one and the run ends holding a report
# written before it finished.
kill "$LIVE_REPORT" 2>/dev/null
wait "$LIVE_REPORT" 2>/dev/null
rm -f "$OUT/.report.next"

# Stop the camera first and let it encode, before anything it films goes away.
for R in $RECORDERS; do kill -INT "$R" 2>/dev/null; done
for R in $RECORDERS; do wait "$R" 2>/dev/null; done
# The per-turn session logs, out of the engines' homes and into the run's
# own folder. Without this a finished run cannot be re-audited: `proof`
# reads docs/runs/series/*/sessions/*.jsonl, and three runs are already
# recorded as unauditable for want of a copy step - the evidence existed
# the whole time, inside engine-homes.tar.gz.
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
