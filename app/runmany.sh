#!/usr/bin/env bash
set -uo pipefail

CAP="${1:?usage: runmany.sh <cap-usd> <minutes> <name>...}"
MINUTES="${2:?}"
shift 2

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

VOID=""
for NAME in "$@"; do
    echo "############ $NAME ############"
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
