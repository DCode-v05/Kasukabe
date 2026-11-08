#!/usr/bin/env bash
# The election run: 20 or 50 residents, long enough for the first ballot to be
# counted, with the town viewer running and recorded to video.
#
#     ./run-election.sh 20 [name] [cap-usd] [max-hours]
#     ./run-election.sh 50 [name] [cap-usd] [max-hours]
#
# 20 runs on a laptop. 50 is for the team box (docs/TEAM_BOX_GUIDE.md): it
# needs ~3.2 GB for its engines at the 64 MB each measured here, and an 8 GB
# laptop with an editor and a browser open has about 1 GB to give.
#
# Environment knobs: PY (the Python to use; defaults to .venv/bin/python,
# then python3), OUT (where logs, video and report go; on the team box use
# ~/results/NAME), CHROME (the browser the recorder drives; found
# automatically), RESUME=1 (continue a run the box's 4-hour limit cut off:
# the checkpoints, the engine homes and the video frames all carry on).
#
# WHY THIS RUN. docs/GOALS.md ("Status on 2026-09-21") names it as the one
# run most of the unticked checklist is waiting for. The first election is
# scheduled at genesis under the default charter: candidacy tick 0-144 (day
# 0), campaign 144-288 (day 1), voting 288-432 (day 2), counted at tick 432
# (game-day 3, 00:00). No run has got past tick 194. This one starts at dawn
# (tick 36) and stops at tick 590 - 01:20 on game-day 4 - so that after the
# count the new government has all of day 3 to act (GOV-3/5/6/7, the policy
# levers), and the day-3 boundary at tick 576 runs: the first tax charged at
# whatever rate it set, the first government wages paid from the treasury
# (ECON-11, X17). STOP_TICK=480 is the cheaper run that ends two hours after
# the count and leaves those items unreached.
#
# THE CHECKLIST. The report this writes opens with every run-decidable item
# of docs/GOALS.md scored from the trace (world/run_checklist.py), so the
# run checks the pending items itself instead of leaving it to a reading.
#
# WHO TAKES PART. Every resident aged 18 or over may stand and vote - the
# charter's suffrage rule (suffrage_min_age 18, residency = being a
# resident). In genesis_20 that is 16 of the 20; in genesis_50 (the cast of
# the 50-resident run of 2026-09-16) it is 34 of the 50.
# Each resident's perception states the rule, the place (in person, at the
# Kasukabe Government Complex) and the deadlines, as facts. Nobody is told
# to stand or to vote.
#
# WHAT YOU WATCH. The town viewer (game server) on http://127.0.0.1:8400/
# township-v2.html, bound to this run's world: residents on the map, talk,
# group chats, and the election - a strip on the map and the Election book
# (candidates, ballots as they come in, the result, then sashes on the
# winners). scripts/record_ui.py films it: a frame every 2 s, stitched to
# $OUT/video/run.mp4 at the end (frames kept, so a killed run still has one).
#
# WHAT IS DIFFERENT FROM run50.sh:
#   * macOS/Linux, not Windows: .venv/bin/python, and it stops only the
#     processes it started (by PID) instead of `taskkill` on every python;
#   * world/genesis_{20,50}_funded.json - $14.00 wallets, because at
#     $2.27-2.84 a resident-day a resident spends $8.70-10.90 reaching tick
#     590. The $1,000 invariant is kept by the treasury ($720 / $300);
#   * --max-ticks $STOP_TICK (590; --max-game-days 5 as a backstop). The cap
#     ($280 for 20, $650 for 50) sits above the expected $175-220 /
#     $440-550, so it cannot stop the run before day 3 is done;
#   * the town viewer and the recorder start with the run and stop with it.
#
# PRECONDITIONS - the run refuses without them:
#   * the broker boots. Without a Qwen profile it runs Kimi + DeepSeek at
#     200 RPM (enough for twenty) and says so in broker.log.
#
# THE KEY GATE IS GONE, ON PURPOSE. This used to refuse without
# CONFIRM_KEY_ROTATED=1, because the key in app/.env was pasted into a chat
# on 2026-09-21. It never checked anything - no gate here can test a key
# without spending money - it only made the operator say the words. Twice it
# was answered untruthfully and the run went ahead, which is what
# docs/runs/series/*/KEY-NOT-ROTATED.txt records. A gate that is routinely
# answered wrong teaches people to answer gates wrong, so it was removed
# rather than left to rot. The risk it named has not changed: a key an admin
# deactivates mid-run stops the run where it stands, with the spend already
# made.
#
#     ./run-election.sh 20 election20 220 12
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
# See run-scenario.sh for why this is not one line: a Windows venv puts the
# interpreter at .venv/Scripts/python.exe and has no `python3`, so trying
# only the posix path left PY empty and the script died several lines later
# with `: command not found`.
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
# The broker's cap sits $10 above the runner's, so the end-of-run learning
# drain has room and the runner's governor is the one that stops the town.
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
    # The box's 4-hour limit: pick up from the last checkpoint. The runner
    # restores the engine homes from the checkpoint directory and the
    # recorder numbers its frames on from the last one it wrote.
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
# The town viewer, bound to THIS run's world (not its mock), and the camera.
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

# Stop on the runner's own exit, on a quiet log (a multiple of the 120s
# tick timeout, never equal to it), or at the wall-clock ceiling.
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

# Stop the camera first and let it encode, before anything it films goes away.
kill -INT "$RECORDER" 2>/dev/null
wait "$RECORDER" 2>/dev/null
# The per-turn session logs, out of the engines' homes and into the run's
# own folder. run-scenario.sh has done this since the three unauditable
# runs were noticed; this launcher never has, which is why `teambox50`
# left a report, a log and a video and nothing that can be read turn by
# turn. The evidence existed the whole time, inside engine-homes.tar.gz -
# what was missing was six lines of copy.
#
# `proof/reach.py` reads exactly these files to decide whether a verb was
# ever reached, so a run that skips this step buys its findings and then
# throws away the only record that can confirm them.
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
