#!/usr/bin/env bash
exec "$(cd "$(dirname "$0")" && pwd)/run-election.sh" 20 "$@"
