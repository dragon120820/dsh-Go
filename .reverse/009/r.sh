# Temporary helpers for reverse-009; sourced by every step after `set +e`.
# R <label> <command string>: run in a subshell, print everything, record the real exit code.
R() {
  local label="$1" cmd="$2"
  local safe
  safe=$(printf '%s' "$label" | tr -c 'A-Za-z0-9' '_')
  LAST="$RUNNER_TEMP/r9-$safe.log"
  echo "R009: BEGIN [$label] :: $cmd"
  ( eval "$cmd" ) > "$LAST" 2>&1
  local rc=$?
  sed 's/^/R009-out| /' "$LAST"
  echo "R009: [$label] exit=$rc"
  if [ "$rc" -ne 0 ]; then echo "$label exit=$rc" >> "$RUNNER_TEMP/r9-fails.txt"; fi
  return 0
}
# S <tag> <extended regex>: print matching lines of the last R log with an R009 prefix.
S() {
  grep -aE "$2" "$LAST" | sed "s/^/R009: $1| /"
}
# Node summary counters and the pass/fail/skip lines
NS() {
  S "$1-counts" '^ℹ (tests|suites|pass|fail|cancelled|skipped|todo) '
  S "$1-fail" '^\s*✖ '
  S "$1-skip" '^\s*﹣ '
}
