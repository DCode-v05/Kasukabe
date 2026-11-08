#!/usr/bin/env bash
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
