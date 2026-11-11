set +e
. .reverse/010/r.sh
F='Expected|Actual|\[E\]|Some tests failed|All tests passed|Error:|error:'
T="$GITHUB_WORKSPACE"
St="$RUNNER_TEMP/start"
R "flutter test 4 files TARGET" "cd '$T/app' && flutter test --no-pub --reporter expanded test/runtime/runtime_bridge_subscription_test.dart test/runtime/runtime_bridge_parsing_test.dart test/update/update_delta_cleanup_test.dart test/update/update_download_size_guard_test.dart"
S target-lines '^[0-9:]+ [+~-]'
S target-fail "$F"
if [ "$R_RC" -ne 0 ]; then tail -150 "$LAST" | sed 's/^/R010: target-tail| /'; fi
# 5317 draft on the clean start tree: the exact file the target used.
cp "$T/app/test/update/update_download_size_guard_test.dart" "$St/app/test/update/update_download_size_guard_test.dart"
echo "R010: 5317-draft-sha256 target=$(sha256sum "$T/app/test/update/update_download_size_guard_test.dart" | cut -d' ' -f1) start=$(sha256sum "$St/app/test/update/update_download_size_guard_test.dart" | cut -d' ' -f1)"
git -C "$St" status --short | sed 's/^/R010: start-status| /'
R "flutter test 5317 draft START" "cd '$St/app' && flutter test --no-pub --reporter expanded test/update/update_download_size_guard_test.dart"
S start-lines '^[0-9:]+ [+~-]'
S start-fail "$F"
if [ "$R_RC" -ne 0 ]; then tail -150 "$LAST" | sed 's/^/R010: start-tail| /'; fi
R "flutter test update regressions TARGET" "cd '$T/app' && flutter test --no-pub --reporter expanded test/update/update_delta_test.dart test/update/downloader_test.dart test/update/update_cached_cancel_test.dart test/update/update_file_completion_cancel_test.dart test/update/update_digest_binding_test.dart test/update/update_ownership_test.dart"
S update-reg-lines '^[0-9:]+ [+~-]'
S update-reg-fail "$F"
if [ "$R_RC" -ne 0 ]; then tail -150 "$LAST" | sed 's/^/R010: update-reg-tail| /'; fi

exit 0
