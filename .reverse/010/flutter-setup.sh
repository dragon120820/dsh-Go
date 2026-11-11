set +e
. .reverse/010/r.sh
echo "R010: job=flutter target=$(git rev-parse HEAD) start=$(git rev-parse $START_SHA)"
echo "R010: id=$(id)"
echo "R010: $(flutter --version 2>&1 | head -3 | tr '\n' ' ')"
echo "R010: $(dart --version 2>&1)"
R "worktree start" "git worktree add --detach '$RUNNER_TEMP/start' $START_SHA"
echo "R010: start-tree head=$(git -C "$RUNNER_TEMP/start" rev-parse HEAD)"
# Target tree gets all four drafts byte for byte.
mkdir -p app/test/runtime app/test/update
cp .reverse/010/owner-tests/runtime_bridge_subscription_test.dart .reverse/010/owner-tests/runtime_bridge_parsing_test.dart app/test/runtime/
cp .reverse/010/owner-tests/update_delta_cleanup_test.dart .reverse/010/owner-tests/update_download_size_guard_test.dart app/test/update/
( cd app/test && sha256sum runtime/runtime_bridge_subscription_test.dart runtime/runtime_bridge_parsing_test.dart update/update_delta_cleanup_test.dart update/update_download_size_guard_test.dart ) | sed 's/^/R010: draft-sha256-as-attached| /'
ls -l app/test/update/support.dart "$RUNNER_TEMP/start/app/test/update/support.dart" | sed 's/^/R010: support| /'
R "pub get target" "cd '$GITHUB_WORKSPACE/app' && flutter pub get --enforce-lockfile"
R "pub get start" "cd '$RUNNER_TEMP/start/app' && flutter pub get --enforce-lockfile"

exit 0
