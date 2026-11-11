set +e
. .reverse/010/r.sh
echo "R010: job=kotlin head=$(git rev-parse HEAD) start=$(git rev-parse $START_SHA)"
echo "R010: id=$(id)"
echo "R010: $(java -version 2>&1 | head -1) | $(gradle --version 2>&1 | grep -E '^Gradle' )"
find "$GITHUB_WORKSPACE" -name 'TEST-*.xml' -path '*testArm64DebugUnitTest*' | sed 's/^/R010: preexisting-xml| /'
R "pub get" "cd app && flutter pub get --enforce-lockfile && printf 'sdk.dir=%s\nflutter.sdk=%s\n' \"\$ANDROID_HOME\" \"\$FLUTTER_ROOT\" > android/local.properties"
R "gradle testArm64DebugUnitTest" "cd app/android && gradle --stacktrace :app:testArm64DebugUnitTest --rerun --tests '*LkgPlanTest' --tests '*LastKnownGoodBootTest'"
S gradle-summary '(FAILED|tests completed|BUILD |Expected|expected|Actual|actual|ComparisonFailure|AssertionError)'
S compile-warnings '^w: '
S compile-warnings-any '(warning:|Warning:)'
find "$GITHUB_WORKSPACE" -name 'TEST-*.xml' -path '*testArm64DebugUnitTest*' | sed 's/^/R010: junit-found| /'
python3 .reverse/010/junit.py $(find "$GITHUB_WORKSPACE" -name 'TEST-*.xml' -path '*testArm64DebugUnitTest*' | sort)
echo "R010: junit python exit=$?"
if [ -s "$RUNNER_TEMP/r10-fails.txt" ]; then echo "R010: job=kotlin FAILED COMMANDS:"; sed 's/^/R010: /' "$RUNNER_TEMP/r10-fails.txt"; exit 1; fi
echo "R010: job=kotlin all commands exit 0"
