#!/usr/bin/env bash
# One iteration of the run series, start to graded report.
#
#     ./runseries.sh <name> <cap-usd> <minutes> [genesis]
#
# Each iteration is a WHOLE run: it starts a broker and a world, lets the
# town live until the spend cap dries out or the clock runs out, stops
# everything, and grades a COMPLETED store. Build Spec v2 section 10.1
# forbids grading a mid-run sample as a result, and this project has made
# that mistake three times, so the report is only ever read after the
# world has stopped.
#
# Everything it writes lands under docs/runs/series/<name>/ so a run can
# be re-read months later: the trace store path, both logs, the gate
# report, and the engines' own session logs.
set -uo pipefail

NAME="${1:?usage: runseries.sh <name> <cap-usd> <minutes> [genesis]}"
CAP="${2:-1}"
MINUTES="${3:-8}"
GENESIS="${4:-world/genesis_10.json}"

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

# One series at a time, and the harness enforces it rather than trusting
# whoever started it to remember.
#
# Four independent series ended up running at once, started minutes apart
# and every one of them holding the same port 8200, the same engine ports,
# the same trace paths and the same output directories. They deleted each
# other's directories mid-run, killed each other's engines with taskkill,
# and served each other's calls into each other's stores - which is what
# "final2 answered four requests 200 OK and its trace holds zero rows"
# actually was. `taskkill //F //IM python.exe` kills the ENGINES but not
# the bash script driving them, so stopping a series by killing python
# leaves the driver alive and it starts the next iteration on top of
# whatever is running.
#
# A lock directory, because mkdir is atomic on every filesystem this runs
# on and a lock file written with > is not.
LOCK="/tmp/kasukabe-runseries.lock"
if ! mkdir "$LOCK" 2>/dev/null; then
    echo "   REFUSED: another series holds $LOCK. One at a time - see the"
    echo "   comment here for what four at once did to the final series."
    exit 2
fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

OUT="../docs/runs/series/$NAME"
HOME_ROOT="/tmp/k_$NAME"
DB="data/$NAME.db"
CENTS=$(python -c "print(int(float('$CAP') * 100))")

mkdir -p "$OUT"
rm -f "$DB"*; rm -rf "$HOME_ROOT"; mkdir -p "$HOME_ROOT"

echo "== $NAME: cap \$$CAP, up to ${MINUTES}m, $GENESIS"

# A previous iteration's engines still holding ports would make this run
# look healthy while measuring the last one. The port check catches it now
# (world/preflight.py) but stopping first is cheaper than being refused.
taskkill //F //IM python.exe //T >/dev/null 2>&1 || true
# Long enough for Windows to actually release 8200 and 9100-9109. A
# previous iteration's broker still holding the port means this run's
# calls are served by IT and written to ITS store, which is how final2
# came out with four requests answered 200 OK in its own broker log and
# zero rows in its own trace.
sleep 12

TRACE_SQLITE_PATH="$DB" SPEND_CAP_USD="$CAP" \
  nohup .venv/Scripts/python.exe -m uvicorn broker.app:app \
  --host 127.0.0.1 --port 8200 > "$OUT/broker.log" 2>&1 &

# Wait for the broker to ANSWER, rather than sleeping a guessed interval.
# A blind sleep is a bet on boot time, and losing it silently starts a
# world with nowhere to think.
for _ in $(seq 1 40); do
    curl -sf -o /dev/null "http://127.0.0.1:8200/health" && break
    sleep 2
done
if ! curl -sf -o /dev/null "http://127.0.0.1:8200/health"; then
    echo "   VOID: the broker never came up. $NAME is not graded." | tee "$OUT/VOID.txt"
    exit 1
fi

KASUKABE_HOME_ROOT="$HOME_ROOT" TRACE_SQLITE_PATH="$DB" SPEND_CAP_USD="$CAP" \
  nohup .venv/Scripts/python.exe -m world.run_genesis \
  --genesis-path "$GENESIS" --world-mode embedded \
  --world-host 127.0.0.1 --world-port 8100 \
  --broker-base-url http://127.0.0.1:8200 \
  --supervisor-backend process --engine-repo ../engine --no-resume \
  --start-at-dawn \
  --tick-interval-s 15 --budget-cap-cents "$CENTS" > "$OUT/runner.log" 2>&1 &

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

# Poll rather than sleep the whole window: the run stops ITSELF when the
# broker halts at the cap and terminal_reason_from_spend shuts the runner
# down, and waiting out the clock after that just delays the report.
#
# Liveness is measured by whether the RUNNER LOG IS STILL GROWING, not by
# asking for the process. `pgrep` does not exist in this Git Bash and
# returns non-zero when asked for anything, so `! pgrep ...` was true on
# the first poll and run2 was killed twenty seconds in with nine calls to
# its name - a harness that reported a dead town rather than measuring
# one. A log that has not moved in QUIET_S is a run that has stopped, and
# it works the same on either platform.
# Comfortably longer than run_genesis's own tick_timeout_s, which is 120.
#
# It WAS 120, exactly equal, and that is a race the harness loses about
# half the time: one turn that takes the full timeout leaves a 120-second
# gap in the log, and the harness reads a gap it caused no part of as a
# stopped run. It killed five healthy runs of the final series at forty
# seconds each and graded them - G6 at 86-96% because the agents were
# busy, three utterances because they had barely started.
#
# That is the second time this harness has reported a dead town rather
# than measuring one, in a second way, so the rule is now explicit: this
# must be a MULTIPLE of the longest a single tick may legitimately take,
# never equal to it.
QUIET_S=300
DEADLINE=$(( $(date +%s) + MINUTES * 60 ))
LAST_SIZE=0
LAST_CHANGE=$(date +%s)
while [ "$(date +%s)" -lt "$DEADLINE" ]; do
    sleep 20
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
sleep 3

# Engine session logs, copied out before the next iteration wipes the home
# root. They are the only record of what a resident actually thought.
mkdir -p "$OUT/sessions"
for d in "$HOME_ROOT"/*/; do
    [ -f "$d/session_log.jsonl" ] && cp "$d/session_log.jsonl" "$OUT/sessions/$(basename "$d").jsonl"
done

# A run that did not happen is not a result.
#
# THIS IS THE THIRD TIME the harness has produced a graded report for a
# town that never ran: once from `pgrep` not existing, once from a quiet
# window exactly equal to the tick timeout, and once from a broker that
# served four requests into somebody else's store while this run's trace
# stayed empty. Each of the three printed six gates and a verdict, and
# every number in them was about the harness.
#
# Build Spec v2 section 10.1 says a sample that is not a run is not a
# result. The harness enforces that now instead of trusting itself to
# notice.
#
# The floor is deliberately low. It is not a quality bar - loop_report.py
# owns those - it is the difference between a run and a corpse.
CALLS=$(.venv/Scripts/python.exe -c "
import sqlite3
try:
    conn = sqlite3.connect('file:$DB?mode=ro', uri=True)
    print(conn.execute('select count(*) from llm_calls').fetchone()[0])
except Exception:
    print(0)
")
if [ "${CALLS:-0}" -lt 40 ]; then
    echo "   VOID: $NAME recorded ${CALLS:-0} calls. A run that did not happen is not a result (Build Spec v2 10.1) and is not graded." | tee "$OUT/VOID.txt"
    exit 1
fi

.venv/Scripts/python.exe loop_report.py "$DB" | tee "$OUT/gates.txt"
echo "   -> $OUT"
