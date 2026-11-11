set +e
. .reverse/010/r.sh
echo "R010: job=dart head=$(git rev-parse HEAD) start=$(git rev-parse $START_SHA)"
echo "R010: id=$(id)"
echo "R010: $(dart --version 2>&1)"
R "dart pub get dsh_client" "cd packages/dsh_client && dart pub get --enforce-lockfile"
R "format dsh_go_api" "dart format --output=none --set-exit-if-changed --line-length=120 packages/dsh_client/lib/src/dsh_go_api.dart"
S format-dsh_go_api '(Changed|Formatted)'
if [ "$R_RC" -ne 0 ]; then
  echo "R010: dart-path=$(command -v dart) resolved=$(readlink -f "$(command -v dart)")"
  echo "R010: cwd=$(pwd)"
  echo "R010: package_config dsh_client entries:"
  python3 -I - <<'PY' | sed 's/^/R010: package_config| /'
import json
for p in ("packages/dsh_client/.dart_tool/package_config.json", ".dart_tool/package_config.json"):
    try:
        d = json.load(open(p))
    except Exception as e:
        print(p, "unreadable:", e)
        continue
    print(p, "generator", d.get("generator"), d.get("generatorVersion"))
    for pk in d["packages"]:
        if pk["name"] == "dsh_client":
            print(p, json.dumps(pk))
PY
  for f in analysis_options.yaml packages/dsh_client/analysis_options.yaml app/analysis_options.yaml packages/dsh_client/lib/analysis_options.yaml packages/dsh_client/lib/src/analysis_options.yaml; do
    if [ -f "$f" ]; then
      echo "R010: analysis_options exists: $f"
      sed "s|^|R010: analysis_options[$f]| " "$f" | head -40
    else
      echo "R010: analysis_options absent: $f"
    fi
  done
  # What the formatter wants, on a temporary copy of the package (package_config is relative, so the copy keeps its language version).
  rm -rf "$RUNNER_TEMP/dc-copy" && mkdir -p "$RUNNER_TEMP/dc-copy/packages" && cp -a packages/dsh_client "$RUNNER_TEMP/dc-copy/packages/"
  [ -f analysis_options.yaml ] && cp analysis_options.yaml "$RUNNER_TEMP/dc-copy/"
  ( cd "$RUNNER_TEMP/dc-copy" && dart format --line-length=120 packages/dsh_client/lib/src/dsh_go_api.dart 2>&1 | sed 's/^/R010: copy-format| /'
    diff -u "$GITHUB_WORKSPACE/packages/dsh_client/lib/src/dsh_go_api.dart" packages/dsh_client/lib/src/dsh_go_api.dart | sed 's/^/R010: format-diff| /' )
  git status --short | sed 's/^/R010: repo-status-after-diff| /'
fi
if [ -s "$RUNNER_TEMP/r10-fails.txt" ]; then echo "R010: job=dart FAILED COMMANDS:"; sed 's/^/R010: /' "$RUNNER_TEMP/r10-fails.txt"; exit 1; fi
echo "R010: job=dart all commands exit 0"
