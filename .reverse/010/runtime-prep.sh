set +e
. .reverse/010/r.sh
echo "R010: job=runtime head=$(git rev-parse HEAD) start=$(git rev-parse $START_SHA)"
echo "R010: id=$(id)"
echo "R010: node=$(node -v) npm=$(npm -v) os=$(. /etc/os-release; echo $PRETTY_NAME)"
grep -E '^Cap(Eff|Prm|Bnd)' /proc/self/status | sed 's/^/R010: /'
version=$(sed -nE 's/^DSH_VERSION="\$\{DSH_VERSION:-(.+)\}"$/\1/p' runtime/build-runtime.sh)
echo "R010: dsh-version=$version"
prefix="$RUNNER_TEMP/dsh"
mkdir "$prefix"
cp "runtime/locks/dsh-$version/package.json" "runtime/locks/dsh-$version/package-lock.json" "$prefix/"
R "prefix npm ci" "npm ci --prefix '$prefix' --ignore-scripts --no-audit --no-fund"
cp "$prefix/node_modules/@deepseek-ai/dsh-fs-local/lib/index.js" "$prefix/fs-local-original.js"
R "prefix patch-dsh" "python3 -I runtime/lib/rtbuild.py patch-dsh --prefix '$prefix' --dsh-version '$version'"
pnpm_version=$(sed -nE 's/^PNPM_VERSION="\$\{PNPM_VERSION:-(.+)\}"$/\1/p' runtime/build-runtime.sh)
pnpm_prefix="$RUNNER_TEMP/pnpm"
mkdir "$pnpm_prefix"
cp "runtime/locks/pnpm-$pnpm_version/package.json" "runtime/locks/pnpm-$pnpm_version/package-lock.json" "$pnpm_prefix/"
R "pnpm npm ci" "npm ci --prefix '$pnpm_prefix' --ignore-scripts --no-audit --no-fund"
cp -a "$prefix" "$RUNNER_TEMP/dsh-clean"
echo "R010: clean prefix copy exit=$?"
{
  echo "DSH_PREFIX=$prefix"
  echo "DSH_TEST_PREFIX=$prefix"
  echo "PNPM_BIN=$pnpm_prefix/node_modules/pnpm/bin/pnpm.mjs"
  echo "DSH_INSTALL=$prefix/node_modules/@deepseek-ai/dsh"
  echo "DSH_TOOLS_DIR=$prefix/node_modules/@deepseek-ai/dsh-tools"
  echo "DSH_FS_LOCAL_ORIGINAL_JS=$prefix/fs-local-original.js"
} >> "$GITHUB_ENV"

exit 0
