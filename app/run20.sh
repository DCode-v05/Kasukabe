#!/usr/bin/env bash
# The 20-resident election run. Kept as a name people already use; the
# launcher itself is run-election.sh, which also does 50.
#
#     ./run20.sh [name] [cap-usd] [max-hours]
exec "$(cd "$(dirname "$0")" && pwd)/run-election.sh" 20 "$@"
