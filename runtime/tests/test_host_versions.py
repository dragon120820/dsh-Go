"""The real versions shell block, local command stand-ins; no bwrap or DSH."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "test-host.sh"
DISPATCH = r'''#!INTERPRETER
from pathlib import Path
import os, signal, sys
name = Path(sys.argv[0]).name
assert sys.argv[1:] == ['--version']
with Path(os.environ['HOST_VERSION_TRACE']).open('a') as out:
    out.write(name + '\n')
if os.environ['HOST_VERSION_FAILURE'] == name:
    print('fixture: ' + name + ' version failed', file=sys.stderr)
    raise SystemExit(72)
if name == 'dsh':
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    count = 4096 if os.environ['HOST_VERSION_LONG'] == '1' else 4
    sys.stdout.write(''.join(f'dsh line {i}: ' + 'x' * 64 + '\n' for i in range(count)))
else:
    print(name + ' fixture version')
'''.replace('INTERPRETER', sys.executable)


@unittest.skipUnless(sys.platform.startswith("linux"), "Linux guest shell fixture")
class HostVersionsTest(unittest.TestCase):
    def run_block(self, failure="none", long=False):
        source = SCRIPT.read_text()
        start = source.index('echo "== versions"\n')
        block = source[start:source.index('\n\n', start)]
        # The source run invocation, guest command and outer fail call stay intact.
        block = block.replace('/etc/os-release', '"$HOST_VERSION_RELEASE"')
        with tempfile.TemporaryDirectory(prefix="host versions-") as directory:
            root = Path(directory)
            commands = root / "bin"
            commands.mkdir()
            for name in ("node", "npm", "dsh"):
                path = commands / name
                path.write_text(DISPATCH)
                path.chmod(0o755)
            release = root / "os-release"
            release.write_text("NAME=fixture\nVERSION=fixture\nEXTRA=not-displayed\n")
            trace = root / "trace"
            env = dict(os.environ, PATH=str(commands) + os.pathsep + os.environ.get("PATH", ""),
                       HOST_VERSION_RELEASE=str(release), HOST_VERSION_TRACE=str(trace),
                       HOST_VERSION_FAILURE=failure, HOST_VERSION_LONG="1" if long else "0")
            for key in ("BASH_ENV", "ENV", "SHELLOPTS", "BASHOPTS"):
                env.pop(key, None)
            # The actual env -i boundary is retained. Only bwrap is replaced by
            # direct invocation, and the last three GUEST placeholders are sliced.
            harness = '''set -euo pipefail
FAILED=0
GUEST=(/usr/bin/env -i "PATH=$PATH" "HOST_VERSION_RELEASE=$HOST_VERSION_RELEASE"
  "HOST_VERSION_TRACE=$HOST_VERSION_TRACE" "HOST_VERSION_FAILURE=$HOST_VERSION_FAILURE"
  "HOST_VERSION_LONG=$HOST_VERSION_LONG" /bin/sh -c unused)
run() { shift; "$@"; }
fail() { printf 'FAIL %s\\n' "$*"; FAILED=1; }
''' + block + '\nexit "$FAILED"\n'
            result = subprocess.run(["bash", "-c", harness], env=env, text=True,
                                    capture_output=True, timeout=10)
            return result, trace.read_text().splitlines()

    def assert_normal(self, long=False):
        result, calls = self.run_block(long=long)
        expected = "== versions\nnode fixture version\nnpm fixture version\n"
        expected += "".join(f"dsh line {i}: " + "x" * 64 + "\n" for i in range(3))
        expected += "NAME=fixture\nVERSION=fixture\n"
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, expected)
        self.assertEqual(result.stderr, "")
        self.assertEqual(calls, ["node", "npm", "dsh"])

    def test_normal_versions_keep_exact_displayed_output(self):
        self.assert_normal()

    def test_long_dsh_output_does_not_turn_truncation_into_failure(self):
        self.assert_normal(long=True)

    def assert_failed(self, command):
        result, calls = self.run_block(failure=command)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("FAIL versions\n", result.stdout)
        self.assertIn(command + " version failed", result.stdout + result.stderr)
        self.assertEqual(calls, ["node", "npm", "dsh"][:["node", "npm", "dsh"].index(command) + 1])
        self.assertNotIn("NAME=fixture", result.stdout)

    def test_node_version_failure_is_classified(self):
        self.assert_failed("node")

    def test_npm_version_failure_is_classified(self):
        self.assert_failed("npm")

    def test_dsh_version_failure_is_classified(self):
        self.assert_failed("dsh")


if __name__ == "__main__":
    unittest.main()
