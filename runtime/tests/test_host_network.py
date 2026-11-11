"""Exercise the real host-smoke guest network block with local command stand-ins.

Only /tmp paths are relocated; no bwrap, guest rootfs, package install or network.
Run: python3 -m unittest discover -s runtime/tests -p test_host_network.py -v
"""
from pathlib import Path
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "test-host.sh"
DISPATCH = r'''#!INTERPRETER
from pathlib import Path
import json, os, shutil, sys
name, args = Path(sys.argv[0]).name, sys.argv[1:]
root = Path(os.environ['NETWORK_BLOCK_TEST_ROOT'])
failure = os.environ['NETWORK_BLOCK_TEST_FAILURE']
with (root / 'calls.jsonl').open('a') as log:
    log.write(json.dumps({'command':name,'args':args})+'\n')
if name in ('python3','python'):
    if '-m' in args and args[args.index('-m')+1]=='venv':
        if failure=='venv':
            print('synthetic venv failure',file=sys.stderr); raise SystemExit(71)
        bindir=Path(args[-1])/'bin'; bindir.mkdir(parents=True)
        for command in ('pip','python'):
            p=bindir/command; shutil.copyfile(root/'bin/dispatch',p); p.chmod(0o755)
    else: print('synthetic python success')
elif name=='pip':
    if failure=='pip' and args==['--version']:
        print('synthetic pip failure',file=sys.stderr); raise SystemExit(72)
    if failure=='pip-install' and args[0]=='install':
        print('synthetic pip-install failure',file=sys.stderr); raise SystemExit(75)
    print('pip synthetic')
elif name=='git':
    if args[0]=='init':
        if failure=='git-init':
            print('synthetic git-init failure',file=sys.stderr); raise SystemExit(74)
        Path(args[-1]).mkdir()
    elif args[0]=='ls-remote':
        if failure=='git':
            print('synthetic git failure',file=sys.stderr); raise SystemExit(73)
        print('123456789abc synthetic-head')
    elif 'commit' in args:
        if failure=='git-commit':
            print('synthetic git-commit failure',file=sys.stderr); raise SystemExit(76)
    elif args[0]=='log': print('1234567 synthetic-commit')
    elif args[0]=='--version': print('git synthetic')
elif name=='apt-get':
    if failure=='apt':
        print('synthetic apt failure',file=sys.stderr); raise SystemExit(100)
    print('synthetic apt success')
else: raise SystemExit(97)
'''.replace('INTERPRETER', sys.executable)


@unittest.skipUnless(sys.platform.startswith("linux"), "Linux guest-shell control-flow test")
class HostNetworkBlockTest(unittest.TestCase):
    def run_block(self, failure):
        source = SCRIPT.read_text()
        heading = source.index('echo "== python / pip / venv / git / certificates / dpkg consistency (network)"')
        start = source.index("/bin/bash -c '\n", heading) + len("/bin/bash -c '\n")
        body = source[start:source.index("\n' && pass", start)]
        with tempfile.TemporaryDirectory(prefix="host network block test ") as tmp:
            root = Path(tmp)
            bindir, private_tmp = root / "bin", root / "tmp"
            bindir.mkdir()
            private_tmp.mkdir()
            if failure == "write-file":
                (private_tmp / "hello.py").mkdir()
            for command in ("dispatch", "python3", "pip", "git", "apt-get"):
                path = bindir / command
                path.write_text(DISPATCH)
                path.chmod(0o755)
            # The production launch uses env -i; do not inherit shell option/init hooks.
            env = dict(os.environ, PATH=str(bindir) + os.pathsep + os.environ.get("PATH", ""),
                       NETWORK_BLOCK_TEST_ROOT=str(root), NETWORK_BLOCK_TEST_FAILURE=failure)
            for key in ("SHELLOPTS", "BASHOPTS", "BASH_ENV", "ENV"):
                env.pop(key, None)
            result = subprocess.run(["bash", "-c", body.replace("/tmp", shlex.quote(str(private_tmp)))],
                                    cwd=root, env=env, text=True, capture_output=True, timeout=10)
            calls = [json.loads(line) for line in (root / "calls.jsonl").read_text().splitlines()]
            return result, calls

    def test_success_keeps_filtered_output_and_reaches_all_checks(self):
        result, calls = self.run_block("none")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), [
            "synthetic python success", "pip synthetic", "git synthetic",
            "synthetic python success", "synthetic python success", "pip synthetic",
            "synthetic python success", "git https ok: 123456789abc",
            "1234567 synthetic-commit", "synthetic apt success",
        ])
        self.assertEqual(calls[-1], {"command": "apt-get", "args": ["check", "-q"]})
        self.assertTrue(any(c["command"] == "pip" and c["args"] == ["install", "-q", "six"] for c in calls))

    def test_pip_pipeline_failure_stops_before_git_and_later_checks(self):
        result, calls = self.run_block("pip")
        self.assertEqual(result.returncode, 72, result.stderr)
        self.assertIn("synthetic pip failure", result.stderr)
        self.assertFalse(any(c["command"] in ("git", "apt-get") for c in calls))
        self.assertFalse(any(c["command"] == "python3" and "venv" in c["args"] for c in calls))

    def test_git_pipeline_failure_stops_before_local_repository_and_apt(self):
        result, calls = self.run_block("git")
        self.assertEqual(result.returncode, 73, result.stderr)
        self.assertIn("synthetic git failure", result.stderr)
        self.assertFalse(any(c["command"] == "git" and c["args"][0] == "init" for c in calls))
        self.assertFalse(any(c["command"] == "apt-get" for c in calls))

    def test_script_file_write_failure_stops_before_python_body(self):
        result, calls = self.run_block("write-file")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual([(c["command"], c["args"]) for c in calls], [
            ("python3", ["--version"]), ("pip", ["--version"]), ("git", ["--version"]),
        ])

    def test_venv_failure_stops_before_pip_install_and_git(self):
        result, calls = self.run_block("venv")
        self.assertEqual(result.returncode, 71, result.stderr)
        self.assertFalse(any(c["command"] == "pip" and c["args"][0] == "install" for c in calls))
        self.assertFalse(any(c["command"] == "git" and c["args"][0] == "ls-remote" for c in calls))
        self.assertFalse(any(c["command"] == "apt-get" for c in calls))

    def test_pip_install_failure_stops_before_venv_python_and_git(self):
        result, calls = self.run_block("pip-install")
        self.assertEqual(result.returncode, 75, result.stderr)
        self.assertFalse(any(c["command"] == "python" for c in calls))
        self.assertFalse(any(c["command"] == "git" and c["args"][0] == "ls-remote" for c in calls))
        self.assertFalse(any(c["command"] == "apt-get" for c in calls))

    def test_git_init_failure_stops_before_commit_and_apt(self):
        result, calls = self.run_block("git-init")
        self.assertEqual(result.returncode, 74, result.stderr)
        self.assertFalse(any(c["command"] == "git" and "commit" in c["args"] for c in calls))
        self.assertFalse(any(c["command"] == "apt-get" for c in calls))

    def test_git_commit_failure_stops_before_log_and_apt(self):
        result, calls = self.run_block("git-commit")
        self.assertEqual(result.returncode, 76, result.stderr)
        self.assertFalse(any(c["command"] == "git" and c["args"][0] == "log" for c in calls))
        self.assertFalse(any(c["command"] == "apt-get" for c in calls))

    def test_final_apt_pipeline_failure_returns_nonzero(self):
        result, calls = self.run_block("apt")
        self.assertEqual(result.returncode, 100, result.stderr)
        self.assertEqual(result.stdout.splitlines()[-1], "synthetic apt failure")
        self.assertEqual(calls[-1], {"command": "apt-get", "args": ["check", "-q"]})


if __name__ == "__main__":
    unittest.main()
