#!/usr/bin/env bash
# Run a simulation from two numbers: how many residents, and what it may spend.
#
#     ./run-sim.sh 20 20              20 residents, $20
#     ./run-sim.sh 20 20 my-run       ... under a name of your choosing
#     PLAN_ONLY=1 ./run-sim.sh 50 20  just show the plan, spend nothing
#
# Everything else is worked out: which cast file has that many people, how
# many ticks the budget buys, how long the election's windows can be and
# still be counted before the money runs out, how long it will take on the
# clock. `world/plan_run.py` does that arithmetic and refuses a plan whose
# vote is never reached, BEFORE anything is spent - the first 20-resident
# day-2 attempt was written by hand to run two whole game-days on $20, which
# the measured rates say costs $69.
#
# Then it hands the scenario to run-scenario.sh, which is still the thing
# that runs a town, and appends the seeded-economy section to the report.
#
# Knobs: RATE (dollars per resident-day, default 2.30 - measured 0.96-2.49),
# DAY (game-day to start on, default 2), HOURS (wall-clock ceiling, default
# 6), NO_ELECTION=1, PLAN_ONLY=1.
set -uo pipefail

AGENTS="${1:-}"
BUDGET="${2:-}"
if [ -z "$AGENTS" ] || [ -z "$BUDGET" ]; then
    echo "usage: ./run-sim.sh <agents> <budget-usd> [run-name]"
    echo "   eg: ./run-sim.sh 20 20"
    exit 2
fi

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"
PY="${PY:-.venv/bin/python}"
[ -x "$PY" ] || PY="$(command -v python3)"

DAY="${DAY:-2}"
RATE="${RATE:-2.30}"
HOURS="${HOURS:-6}"
NAME="${3:-run-${AGENTS}a-$(printf '%.0f' "$BUDGET")usd-day${DAY}}"
SCENARIO="world/scenarios/${NAME}.json"
ELECTION_FLAG=""
[ "${NO_ELECTION:-}" = "1" ] && ELECTION_FLAG="--no-election"

echo "== planning: $AGENTS residents, \$$BUDGET"
if ! "$PY" -m world.plan_run --agents "$AGENTS" --budget "$BUDGET" \
        --rate "$RATE" --day "$DAY" --name "$NAME" --out "$SCENARIO" $ELECTION_FLAG; then
    echo
    echo "   REFUSED: this plan never reaches its own vote. Nothing was spent."
    echo "   Raise the budget, cut the cast, or NO_ELECTION=1 to run anyway."
    exit 1
fi

if [ "${PLAN_ONLY:-}" = "1" ]; then
    echo
    echo "   PLAN_ONLY: scenario written to $SCENARIO, nothing spent."
    exit 0
fi

echo
echo "== running"
./run-scenario.sh "$SCENARIO" "$NAME" "$HOURS"
STATUS=$?

# The report is the deliverable, so say plainly whether it exists rather than
# leaving the caller to find out. A run cut short still has a trace worth
# reporting on - see Run-commands/README.md for regenerating it by hand.
OUT="../docs/runs/series/$NAME"
if [ -f "$OUT/report.md" ]; then
    "$PY" -m world.trial_section --db "data/$NAME.db" >> "$OUT/report.md" 2>/dev/null \
        && echo "   appended the seeded-economy section"
    echo "== report   $OUT/report.md ($(wc -l < "$OUT/report.md" | tr -d ' ') lines)"
    for V in "$OUT"/video/*/run.mp4; do
        [ -f "$V" ] && echo "   video    $V ($(du -h "$V" | cut -f1))"
    done
    echo "   sessions $(ls "$OUT/sessions" 2>/dev/null | wc -l | tr -d ' ') per-resident log(s)"
else
    echo "== NO REPORT at $OUT/report.md - the run was cut before it was written."
    echo "   Regenerate: see Run-commands/README.md."
fi
exit $STATUS
