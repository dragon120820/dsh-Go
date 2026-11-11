set +e
. .reverse/010/r.sh
R "media npm install" "cd runtime/media/dsh-media && npm install --ignore-scripts --no-package-lock --no-audit --no-fund"
git status --short | sed 's/^/R010: status-after-install| /'
tools_json=runtime/media/dsh-media/node_modules/@deepseek-ai/dsh-tools/package.json
if [ -f "$tools_json" ]; then
  echo "R010: dsh-tools(media node_modules) version=$(node -p "require('./$tools_json').version") main=$(node -p "require('./$tools_json').main") exports=$(node -p "JSON.stringify(require('./$tools_json').exports)")"
else
  echo "R010: dsh-tools not in media node_modules ($tools_json missing)"
fi
echo "R010: dsh-tools resolved-from-media=$(cd runtime/media/dsh-media && node --input-type=module -e "console.log(import.meta.resolve('@deepseek-ai/dsh-tools'))" 2>&1 | head -3)"
echo "R010: DSH_TOOLS_DIR=$DSH_TOOLS_DIR version=$(node -p "require('$DSH_TOOLS_DIR/package.json').version" 2>&1)"
R "media npm test" "cd runtime/media/dsh-media && npm test -- --test-reporter=spec"
S media-counts '^ℹ (tests|suites|pass|fail|cancelled|skipped|todo) '
S media-fail '^\s*✖ '
S media-skip '^\s*﹣ '
S media-completed-video '(completed video|completed-video)'
if [ "$R_RC" -ne 0 ]; then
  grep -a -n -m3 'Error\|error:' "$LAST" | sed 's/^/R010: media-first-error-lines| /'
  # Earliest child diagnostics: run the child fixture directly for each scenario (supplementary, not part of the named command).
  for sc in normal local-storage-failure query-outage terminal-failed cancel-after-success local-download-timeout overall-timeout-after-success confirmed-cancel cleanup-priority; do
    echo "R010: child-direct scenario=$sc"
    ( cd runtime/media/dsh-media && timeout 30 node test/fixtures/completed-video-child.mjs "$sc" x > "$RUNNER_TEMP/child-$sc.out" 2> "$RUNNER_TEMP/child-$sc.err"; echo "R010: child-direct scenario=$sc exit=$?" )
    head -c 1500 "$RUNNER_TEMP/child-$sc.err" | sed "s/^/R010: child-stderr[$sc]| /"
    head -c 400 "$RUNNER_TEMP/child-$sc.out" | sed "s/^/R010: child-stdout[$sc]| /"
  done
fi
git status --short | sed 's/^/R010: status-after-media| /'

exit 0
