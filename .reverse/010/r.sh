# Temporary helpers for reverse-010; sourced by every step after `set +e`.
# R <label> <command string>: run in a subshell, print everything, record the real exit code.
R() {
  local label="$1" cmd="$2"
  local safe
  safe=$(printf '%s' "$label" | tr -c 'A-Za-z0-9' '_')
  LAST="$RUNNER_TEMP/r10-$safe.log"
  echo "R010: BEGIN [$label] :: $cmd"
  ( eval "$cmd" ) > "$LAST" 2>&1
  local rc=$?
  sed 's/^/R010-out| /' "$LAST"
  echo "R010: [$label] exit=$rc"
  if [ "$rc" -ne 0 ]; then echo "$label exit=$rc" >> "$RUNNER_TEMP/r10-fails.txt"; fi
  R_RC=$rc
  return 0
}
# S <tag> <extended regex>: print matching lines of the last R log with an R010 prefix.
S() {
  grep -aE "$2" "$LAST" | sed "s/^/R010: $1| /"
}
# Node summary counters and the pass/fail/skip lines
NS() {
  S "$1-counts" '^ℹ (tests|suites|pass|fail|cancelled|skipped|todo) '
  S "$1-fail" '^\s*✖ '
  S "$1-skip" '^\s*﹣ '
}
