#!/usr/bin/env bash
# Several whole iterations, back to back.
#
#     ./runmany.sh <cap-usd> <minutes> <name> [<name> ...]
#
# Sequential and never parallel: every run binds port 8200 for the broker
# and 9100-9109 for the engines, so two at once would measure each other.
# The preflight port check now actually detects that (world/preflight.py)
# and would refuse the second - correctly, and after a wasted boot.
set -uo pipefail

CAP="${1:?usage: runmany.sh <cap-usd> <minutes> <name>...}"
MINUTES="${2:?}"
shift 2

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

VOID=""
for NAME in "$@"; do
    echo "############ $NAME ############"
    # A VOID iteration exits non-zero and is RETRIED once, because the
    # causes seen so far - a port not yet released, a broker that lost a
    # race - are transient and a retry costs one run rather than a hole
    # in the series.
    if ! bash runseries.sh "$NAME" "$CAP" "$MINUTES"; then
        echo "############ $NAME was void - retrying once ############"
        if ! bash runseries.sh "$NAME" "$CAP" "$MINUTES"; then
            VOID="$VOID $NAME"
        fi
    fi
done
if [ -n "$VOID" ]; then
    echo "############ VOID and NOT in the series:$VOID ############"
fi
echo "############ series complete: $* ############"
