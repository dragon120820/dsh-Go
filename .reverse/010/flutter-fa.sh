set +e
. .reverse/010/r.sh
cd "$GITHUB_WORKSPACE/app"
prod="lib/runtime/runtime_bridge.dart lib/update/downloader.dart"
drafts="test/runtime/runtime_bridge_subscription_test.dart test/runtime/runtime_bridge_parsing_test.dart test/update/update_delta_cleanup_test.dart test/update/update_download_size_guard_test.dart"
six="$prod $drafts"
mkdir -p "$RUNNER_TEMP/orig-drafts"
for f in $drafts; do mkdir -p "$RUNNER_TEMP/orig-drafts/$(dirname $f)"; cp "$f" "$RUNNER_TEMP/orig-drafts/$f"; done
R "format 6 files, no write (initial)" "dart format --output=none --set-exit-if-changed --line-length=120 $six"
S format6-initial '(Changed|Formatted)'
if [ "$R_RC" -ne 0 ] && grep -aq '^Changed test/' "$LAST"; then
  echo "R010: drafts still Changed; writing ONLY the four drafts with the same formatter"
  R "format 4 drafts WRITE" "dart format --line-length=120 $drafts"
  S format4-write '(Changed|Formatted)'
  for f in $drafts; do
    echo "R010: draft-diff-begin $f"
    diff -u "$RUNNER_TEMP/orig-drafts/$f" "$f" | sed "s|^|R010: draft-diff[$f]| "
    echo "R010: draft-diff-end $f"
  done
  for f in $drafts; do
    echo "R010: draft-sha256 attached=$(sha256sum "$RUNNER_TEMP/orig-drafts/$f" | cut -d' ' -f1) formatted=$(sha256sum "$f" | cut -d' ' -f1) $f"
  done
  R "format 6 files, no write (after draft write)" "dart format --output=none --set-exit-if-changed --line-length=120 $six"
  S format6-after '(Changed|Formatted)'
else
  echo "R010: no draft was written by the formatter"
fi
for f in $drafts; do echo "R010: draft-sha256-final $(sha256sum "$f" | cut -d' ' -f1) $f"; done
git status --short | sed 's/^/R010: status-after-format| /'
R "flutter analyze 6 files" "flutter analyze --no-pub $six"
S analyze6 '(error|warning|info|No issues|issues found|[a-z_]+ - )'

exit 0
