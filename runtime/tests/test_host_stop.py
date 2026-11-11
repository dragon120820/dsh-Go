#!/usr/bin/env python3
"""Linux: original host stop block, owned Python children, no bwrap or DSH."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest


SOURCE = Path(__file__).resolve().parents[1] / 'test-host.sh'
CHILD = '''import os, pathlib, signal, sys, time
mode, ready = sys.argv[1:]
if mode != 'default-term':
    status = {'normal-zero': 0, 'handler-seven': 7}[mode]
    def stop(signum, frame):
        raise SystemExit(status)
    signal.signal(signal.SIGTERM, stop)
pathlib.Path(ready).write_text(str(os.getpid()) + '\\n')
while True:
    time.sleep(60)
'''


@unittest.skipUnless(sys.platform.startswith('linux'), 'Linux /proc and signal contract')
class HostStopTests(unittest.TestCase):
    def run_stop(self, mode):
        source = SOURCE.read_text()
        start = source.index('pid="$(cat "$ROOT/tmp/dsh-web.pid"')
        stop = source.index('\necho "== dsh web offline', start)
        verdicts = '\n'.join(line for line in source.splitlines()
                             if line.startswith(('pass() {', 'fail() {', 'FAILED=0')))
        script = '''set -euo pipefail
ROOT=$1
python_bin=$2
mode=$3
''' + verdicts + '''
run() { "$python_bin" "$ROOT/lib/bin.js" "$mode" "$ROOT/ready"; }
run &
BG=$!
cleanup() {
  if jobs -pr | grep -qx "$BG"; then
    kill -KILL "$BG" 2>/dev/null || true
    wait "$BG" 2>/dev/null || true
  fi
}
trap cleanup EXIT
for _ in $(seq 1 200); do
  [[ -s "$ROOT/ready" ]] && break
  sleep 0.01
done
[[ -s "$ROOT/ready" ]]
cp "$ROOT/ready" "$ROOT/tmp/dsh-web.pid"
''' + source[start:stop] + '\nexit "$FAILED"\n'
        with tempfile.TemporaryDirectory(prefix='dsh-host-stop-test-') as temporary:
            root = Path(temporary)
            (root / 'lib').mkdir()
            (root / 'tmp').mkdir()
            # The name satisfies the existing cmdline predicate; Python runs
            # our fixture, never a copied DSH or downloaded Node program.
            (root / 'lib/bin.js').write_text(CHILD)
            shell = root / 'stop.sh'
            shell.write_text(script)
            process = subprocess.Popen(
                ['bash', str(shell), str(root), sys.executable, mode],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, start_new_session=True)
            try:
                stdout, stderr = process.communicate(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate()
                raise
            child = int((root / 'ready').read_text())
            self.assertFalse(Path(f'/proc/{child}').exists(), 'owned fixture child remains')
            return process.returncode, stdout, stderr

    def test_zero_exit_is_success(self):
        code, stdout, stderr = self.run_stop('normal-zero')
        self.assertEqual(code, 0, (stdout, stderr))
        self.assertIn('dsh exited after SIGTERM (rc=0)', stdout)

    def test_nonzero_handler_exit_is_failure(self):
        code, stdout, stderr = self.run_stop('handler-seven')
        self.assertEqual(code, 1, (stdout, stderr))
        self.assertIn('dsh exited after SIGTERM (rc=7)', stdout)

    def test_unhandled_term_is_failure(self):
        code, stdout, stderr = self.run_stop('default-term')
        self.assertEqual(code, 1, (stdout, stderr))
        self.assertIn('dsh exited after SIGTERM (rc=143)', stdout)


if __name__ == '__main__':
    unittest.main()
