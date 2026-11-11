set +e
. .reverse/010/r.sh
rm -rf "$DSH_PREFIX" && cp -a "$RUNNER_TEMP/dsh-clean" "$DSH_PREFIX"
echo "R010: env DSH_PREFIX=$DSH_PREFIX DSH_TEST_PREFIX=$DSH_TEST_PREFIX DSH_INSTALL=$DSH_INSTALL DSH_TOOLS_DIR=$DSH_TOOLS_DIR DSH_FS_LOCAL_ORIGINAL_JS=$DSH_FS_LOCAL_ORIGINAL_JS UV_USE_IO_URING=$UV_USE_IO_URING"
R "section1 node 4 files" "node --test --test-reporter=spec runtime/tests/native-capabilities.test.mjs runtime/tests/native-micro-generation.test.mjs runtime/tests/plugin-config-recovery.test.mjs runtime/tests/plugin-config-bundles.test.mjs"
S s1-counts '^ℹ (tests|suites|pass|fail|cancelled|skipped|todo) '
S s1-fail '^\s*✖ '
S s1-skip '^\s*﹣ '
S s1-named '(speechStart clears audio role|unreadable .* recovery|baseline read failure)'
if [ "$R_RC" -ne 0 ]; then
  echo "R010: s1 earliest failure context follows"
  grep -a -n -m1 '✖' "$LAST" | sed 's/^/R010: s1-first-fail-line| /'
  grep -a -B2 -A25 -m1 'failureType\|AssertionError\|Error:' "$LAST" | sed 's/^/R010: s1-first-error| /' | head -60
fi

exit 0
