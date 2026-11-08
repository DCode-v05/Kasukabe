#!/usr/bin/env bash
# Local CI. This is the whole gate: there is no GitHub Actions run behind it.
#
# Everything here used to live in .github/workflows/{test,style,security}.yml.
# Two things changed in the move, both deliberate:
#
#   1. Trivy scanned the image twice, once to fail on CRITICAL and once to print
#      HIGH. That is two full scans of one image for one report. It now scans
#      once and splits the output.
#   2. `docker compose run --rm test` defaults to a bare pytest with no coverage,
#      while CI ran it with --cov-fail-under=72 and the stress ignores. The two
#      had silently diverged, so "tests pass locally" did not mean "tests pass in
#      CI". The `test` target below runs the CI invocation, exactly.
#
# Usage:
#   scripts/ci.sh              run everything
#   scripts/ci.sh lint types   run only those targets
#   scripts/ci.sh --list       show targets
#
# Every target is independent and each records pass/fail/skip; the run does not
# stop at the first failure, because knowing all of what is broken beats knowing
# the first thing. Exit status is non-zero if anything failed OR was skipped for
# a missing tool: a gate that did not run is not a gate that passed.

set -uo pipefail
cd "$(dirname "$0")/.."

RUFF_VERSION=0.15.16      # keep in step with pyproject [dev] and .pre-commit-config.yaml
GITLEAKS_VERSION=8.30.1
COV_FLOOR=72
TEST_IMAGE=september-engine-test:local

# Vulnerabilities suppressed for the audit gate. EMPTY, and it should stay that
# way: cryptography>=48.0.1 and Pillow>=12.3.0 cleared the entire backlog that
# used to live here. Add an entry only with a dated reason and a plan to remove
# it; anything NOT listed here fails the run.
PIP_AUDIT_IGNORES=()

RED=$'\033[31m'; GRN=$'\033[32m'; YEL=$'\033[33m'; DIM=$'\033[2m'; OFF=$'\033[0m'
[ -t 1 ] || { RED=""; GRN=""; YEL=""; DIM=""; OFF=""; }

declare -a RESULTS=()
FAILED=0

record() {  # record <status> <target> [note]
  RESULTS+=("$1|$2|${3:-}")
  case "$1" in
    pass) printf "  %sPASS%s  %s\n" "$GRN" "$OFF" "$2" ;;
    fail) printf "  %sFAIL%s  %s %s\n" "$RED" "$OFF" "$2" "${3:-}"; FAILED=1 ;;
    skip) printf "  %sSKIP%s  %s %s%s%s\n" "$YEL" "$OFF" "$2" "$DIM" "${3:-}" "$OFF"; FAILED=1 ;;
  esac
}

have() { command -v "$1" >/dev/null 2>&1; }

banner() { printf "\n%s>> %s%s\n" "$DIM" "$1" "$OFF"; }

# --- targets ----------------------------------------------------------------

t_lint() {
  banner "ruff check + ruff format --check (src/ tests/)"
  have ruff || { record skip ruff "ruff not installed: pip install ruff==$RUFF_VERSION"; return; }
  local got; got="$(ruff --version | awk '{print $2}')"
  [ "$got" = "$RUFF_VERSION" ] || printf "  %swarning: ruff %s installed, CI pinned %s; formatting differs between versions%s\n" \
    "$YEL" "$got" "$RUFF_VERSION" "$OFF"
  ruff check src/ tests/ && record pass "ruff check" || record fail "ruff check"
  ruff format --check src/ tests/ && record pass "ruff format" || record fail "ruff format"
}

t_types() {
  banner "mypy (src/)"
  have mypy || { record skip mypy 'mypy not installed: pip install -e ".[dev]"'; return; }
  mypy && record pass mypy || record fail mypy
}

t_style() {
  # Diff-scoped house gate: no em dashes, no new leading-underscore names, judged
  # only on the lines this change ADDS. Base defaults to HEAD (what you are about
  # to commit); pass CI_STYLE_BASE=origin/main to judge a whole branch.
  banner "house style (diff-scoped)"
  python3 scripts/check_style_diff.py --base "${CI_STYLE_BASE:-HEAD}" \
    && record pass style || record fail style
}

t_test() {
  # The CI invocation, verbatim. Docker only: the sandbox tests fail-close unless
  # bubblewrap can create namespaces and fork(), which needs the privileged
  # container from docker-compose.yml.
  banner "pytest + coverage (privileged container)"
  have docker || { record skip pytest "docker not available"; return; }
  docker compose run --rm test \
    pytest tests/ -q --tb=short \
      --ignore=tests/stress_engine_citadel.py \
      --ignore=tests/stress_live_llm.py \
      --cov=src --cov-report=term --cov-fail-under=$COV_FLOOR \
    && record pass "pytest (cov>=$COV_FLOOR%)" || record fail "pytest (cov>=$COV_FLOOR%)"
}

t_image() {
  # One scan, two readings. CRITICAL blocks; HIGH is reported but does not fail,
  # so a single unpatched HIGH in a base layer cannot wedge everything.
  banner "trivy image scan"
  have docker || { record skip trivy "docker not available"; return; }
  docker image inspect "$TEST_IMAGE" >/dev/null 2>&1 \
    || { record skip trivy "$TEST_IMAGE not built: docker compose build test"; return; }

  local json; json="$(mktemp)"
  if ! docker run --rm -v /var/run/docker.sock:/var/run/docker.sock \
        aquasec/trivy:latest image --quiet --format json --ignore-unfixed \
        --vuln-type os,library --severity HIGH,CRITICAL "$TEST_IMAGE" >"$json" 2>/dev/null; then
    record skip trivy "scan could not run (no network, or image pull failed)"
    rm -f "$json"; return
  fi

  local crit high
  crit=$(grep -o '"Severity": *"CRITICAL"' "$json" | wc -l | tr -d ' ')
  high=$(grep -o '"Severity": *"HIGH"' "$json" | wc -l | tr -d ' ')
  printf "  %sHIGH: %s (report only)   CRITICAL: %s (blocking)%s\n" "$DIM" "$high" "$crit" "$OFF"
  if [ "$crit" -gt 0 ]; then
    docker run --rm -v /var/run/docker.sock:/var/run/docker.sock aquasec/trivy:latest \
      image --quiet --format table --ignore-unfixed --vuln-type os,library \
      --severity CRITICAL "$TEST_IMAGE" 2>/dev/null
    record fail trivy "$crit critical"
  else
    record pass "trivy (0 critical, $high high)"
  fi
  rm -f "$json"
}

t_audit() {
  banner "pip-audit (resolved dependency tree)"
  have pip-audit || { record skip pip-audit "not installed: pip install pip-audit"; return; }
  # ${arr[@]+"${arr[@]}"} keeps `set -u` from treating an EMPTY array as an
  # unbound variable — bash 3.2 (still the macOS system bash) does exactly that,
  # so a clean ignore list would otherwise crash this target.
  local args=()
  for v in ${PIP_AUDIT_IGNORES[@]+"${PIP_AUDIT_IGNORES[@]}"}; do args+=(--ignore-vuln "$v"); done
  pip-audit ${args[@]+"${args[@]}"} && record pass pip-audit || record fail pip-audit
}

t_secrets() {
  # Scans COMMITTED history, not the working tree, so a local gitignored .env is
  # never seen. A secret committed once and later removed stays a finding until
  # allowlisted in .gitleaks.toml or removed from history.
  banner "gitleaks (committed history)"
  have gitleaks || { record skip gitleaks "not installed: brew install gitleaks (CI pinned v$GITLEAKS_VERSION)"; return; }
  gitleaks detect --source . --config .gitleaks.toml --redact --no-banner \
    && record pass gitleaks || record fail gitleaks
}

t_deps() {
  # What .github/dependabot.yml used to do weekly: tell you which pinned
  # dependencies have moved. It cannot open the PR for you, so this reports and
  # never fails the run - an outdated dependency is information, not a defect.
  # pip-audit above is the gate; this is the nudge behind it.
  banner "outdated dependencies (report only)"
  local pip; pip="$(command -v pip3 || command -v pip)"
  if [ -z "$pip" ]; then record skip deps "pip not found"; return; fi
  local out; out="$($pip list --outdated --format=columns 2>/dev/null)"
  if [ -z "$out" ]; then
    record pass "deps (none outdated)"
  else
    printf "%s\n" "$out" | sed 's/^/  /'
    record pass "deps ($(( $(printf "%s\n" "$out" | wc -l) - 2 )) outdated, informational)"
  fi
  printf "  %sbase images: docker compose build --pull test%s\n" "$DIM" "$OFF"
}

TARGETS=(lint types style test image audit secrets deps)
DEFAULT=(lint types style test image audit secrets)   # deps is informational, ask for it explicitly

# --- driver -----------------------------------------------------------------

if [ "${1:-}" = "--list" ]; then printf '%s\n' "${TARGETS[@]}"; exit 0; fi

run=("$@"); [ ${#run[@]} -eq 0 ] && run=("${DEFAULT[@]}")

for t in "${run[@]}"; do
  case " ${TARGETS[*]} " in
    *" $t "*) "t_$t" ;;
    *) printf "%sunknown target: %s%s (try --list)\n" "$RED" "$t" "$OFF"; exit 2 ;;
  esac
done

printf "\n%s%s%s\n" "$DIM" "----------------------------------------" "$OFF"
for r in "${RESULTS[@]}"; do
  IFS='|' read -r st name note <<<"$r"
  case "$st" in
    pass) printf "  %sPASS%s  %s\n" "$GRN" "$OFF" "$name" ;;
    fail) printf "  %sFAIL%s  %s %s\n" "$RED" "$OFF" "$name" "$note" ;;
    skip) printf "  %sSKIP%s  %s %s%s%s\n" "$YEL" "$OFF" "$name" "$DIM" "$note" "$OFF" ;;
  esac
done

if [ $FAILED -ne 0 ]; then
  printf "\n%sci: NOT clean%s (a skipped gate counts as not clean: it did not run)\n" "$RED" "$OFF"
  exit 1
fi
printf "\n%sci: clean%s\n" "$GRN" "$OFF"
