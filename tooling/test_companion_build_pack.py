"""Linux publication checks; real pack builder, synthetic assets and local fake npx.

    python3 -m unittest discover -s tooling -p test_companion_build_pack.py -v
"""

import hashlib
import io
import os
from pathlib import Path
import random
import shutil
import signal
import subprocess
import sys
import tempfile
import unittest
import zipfile

if sys.platform.startswith("linux"):
    import resource

SCRIPT = Path(__file__).resolve().parent / "companion" / "build-pack.mjs"
NODE = shutil.which("node")


def limit_file_size():
    # Per-child limit produces a real partial write + EFBIG, without filling a disk.
    signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
    resource.setrlimit(resource.RLIMIT_FSIZE, (4096, 4096))


@unittest.skipUnless(sys.platform.startswith("linux") and NODE, "requires Linux and host Node")
class CompanionBuildPackTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="companion-pack-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        npx = self.bin / "npx"
        npx.write_text("#!/bin/sh\nfor arg; do\n  case \"$arg\" in\n    --outfile=*) printf '/* synthetic framework */' > \"${arg#--outfile=}\"; exit ;;\n  esac\ndone\nexit 1\n")
        npx.chmod(0o755)
        self.framework = self.root / "framework"
        (self.framework / "Shaders/WebGL").mkdir(parents=True)
        (self.framework / "Shaders/WebGL/one.glsl").write_text("void main() {}\n")
        (self.framework / "LICENSE.md").write_text("Synthetic local fixture.\n")
        (self.root / "core.js").write_text("/* synthetic core */\n")
        self.character = self.root / "character"
        self.character.mkdir()
        rng = random.Random(5057)
        for i in range(20):
            (self.character / f"piece-{i:02}.bin").write_bytes(rng.randbytes(700))
        (self.root / "config.json").write_text('{"fixture": true}\n')
        self.work = self.root / "work"
        self.work.mkdir()
        self.output = self.root / "out/pack.zip"
        self.env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ.get("PATH", ""), TMPDIR=str(self.work))

    def build(self, *, limited=False, relative=False):
        out = self.output.relative_to(self.root) if relative else self.output
        return subprocess.run(
            [NODE, str(SCRIPT), "--core", "core.js", "--framework", str(self.framework),
             "--character", str(self.character), "--config", "config.json", "--out", str(out)],
            cwd=self.root, env=self.env, text=True, capture_output=True, timeout=10,
            preexec_fn=limit_file_size if limited else None,
        )

    def assert_clean(self, expected_output):
        self.assertEqual(sorted(p.name for p in self.output.parent.iterdir()), expected_output)
        self.assertEqual(list(self.work.iterdir()), [])

    def assert_complete(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        data = self.output.read_bytes()
        self.assertGreater(len(data), 4096)
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            self.assertIsNone(z.testzip())
            self.assertEqual(z.read("character/character.json"), (self.root / "config.json").read_bytes())
            self.assertEqual(z.read("character/piece-00.bin"), (self.character / "piece-00.bin").read_bytes())
            digest = hashlib.sha256()
            for name in sorted(n for n in z.namelist() if n.startswith("runtime/")):
                digest.update(f"{name}\0{hashlib.sha256(z.read(name)).hexdigest()}\n".encode())
            self.assertIn(f"runtime digest  {digest.hexdigest()}", result.stdout)
        self.assertIn(f"bytes           {len(data)}", result.stdout)
        self.assertIn(f"sha256          {hashlib.sha256(data).hexdigest()}", result.stdout)
        self.assert_clean(["pack.zip"])
        return data

    def test_complete_zip_replaces_existing_relative_output(self):
        before = self.assert_complete(self.build(relative=True))
        self.assertEqual(self.assert_complete(self.build(relative=True)), before)
        (self.character / "piece-00.bin").write_bytes(b"changed local model fixture")
        self.assertNotEqual(self.assert_complete(self.build(relative=True)), before)

    def test_relative_symlink_to_existing_target_is_preserved(self):
        before = self.assert_complete(self.build())
        target = self.root / "linked/target.zip"
        target.parent.mkdir()
        self.output.rename(target)
        self.output.symlink_to("../linked/target.zip")
        (self.character / "piece-00.bin").write_bytes(b"updated linked model")
        after = self.assert_complete(self.build(relative=True))
        self.assertTrue(self.output.is_symlink())
        self.assertEqual(os.readlink(self.output), "../linked/target.zip")
        self.assertEqual(target.read_bytes(), after)
        self.assertNotEqual(after, before)

    def test_relative_dangling_symlink_creates_target(self):
        self.output.parent.mkdir()
        target = self.root / "linked/target.zip"
        target.parent.mkdir()
        self.output.symlink_to("../linked/target.zip")
        data = self.assert_complete(self.build(relative=True))
        self.assertTrue(self.output.is_symlink())
        self.assertEqual(os.readlink(self.output), "../linked/target.zip")
        self.assertEqual(target.read_bytes(), data)

    def test_hard_link_observes_update(self):
        before = self.assert_complete(self.build())
        alias = self.root / "alias.zip"
        os.link(self.output, alias)
        (self.character / "piece-00.bin").write_bytes(b"updated hard linked model")
        after = self.assert_complete(self.build())
        self.assertEqual(self.output.stat().st_ino, alias.stat().st_ino)
        self.assertEqual(alias.read_bytes(), after)
        self.assertNotEqual(after, before)

    @unittest.skipIf(sys.platform.startswith("linux") and os.geteuid() == 0, "file permission check requires non-root")
    def test_existing_mode_and_write_denial_are_preserved(self):
        self.assert_complete(self.build())
        self.output.chmod(0o640)
        self.assert_complete(self.build())
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o640)
        before = self.output.read_bytes()
        self.output.chmod(0o444)
        failed = self.build()
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("EACCES", failed.stderr)
        self.assertEqual(self.output.read_bytes(), before)
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o444)
        self.assert_clean(["pack.zip"])

    @unittest.skipIf(sys.platform.startswith("linux") and os.geteuid() == 0, "directory permission check requires non-root")
    def test_writable_file_in_read_only_directory_keeps_working(self):
        before = self.assert_complete(self.build())
        self.output.parent.chmod(0o555)
        try:
            (self.character / "piece-00.bin").write_bytes(b"changed in read only directory")
            self.assertNotEqual(self.assert_complete(self.build()), before)
        finally:
            self.output.parent.chmod(0o755)

    def test_partial_write_preserves_previous_zip(self):
        before = self.assert_complete(self.build())
        failed = self.build(limited=True)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("EFBIG", failed.stderr)
        self.assertEqual(self.output.read_bytes(), before)
        self.assert_clean(["pack.zip"])

    def test_partial_write_leaves_no_new_output(self):
        failed = self.build(limited=True)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("EFBIG", failed.stderr)
        self.assertFalse(self.output.exists())
        self.assert_clean([])

    def test_publish_error_keeps_directory_and_cleans_temporary_files(self):
        self.output.mkdir(parents=True)
        sentinel = self.output / "keep"
        sentinel.write_bytes(b"existing output directory")
        failed = self.build()
        self.assertNotEqual(failed.returncode, 0)
        self.assertEqual(sentinel.read_bytes(), b"existing output directory")
        self.assert_clean(["pack.zip"])


if __name__ == "__main__":
    unittest.main()
