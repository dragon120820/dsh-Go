#!/usr/bin/env python3
"""Unit tests for `rtbuild.py patch-dsh` (strict unified-diff application, per-version pins).

    python3 -I runtime/tests/test_patch_dsh.py
"""
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True   # runtime/lib/__pycache__ is tracked; do not rewrite it
HERE = os.path.dirname(os.path.abspath(__file__))
RTBUILD = os.path.join(HERE, "..", "lib", "rtbuild.py")
spec = importlib.util.spec_from_file_location("rtbuild", RTBUILD)
rtbuild = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rtbuild)

REL = "node_modules/@x/web/lib/index.js"
ORIGINAL = "".join("line %d\n" % i for i in range(1, 41)).replace("line 20\n", "\tif (route) {\n\t\tawait route.handler(req, res);\n\t}\n")
DIFF = """# comment lines before the first header are ignored
--- a/%s
+++ b/%s
@@ -19,4 +19,5 @@
 line 19
 \tif (route) {
+\t\tif (await guard(req, res)) return;
 \t\tawait route.handler(req, res);
 \t}
@@ -40,1 +41,2 @@
 line 40
+-- a removed-looking added line
""" % (REL, REL)
sha = lambda text: hashlib.sha256(text.encode()).hexdigest()


def read(path):
    with open(path) as f:
        return f.read()


def load(path):
    with open(path) as f:
        return json.load(f)


class PatchDsh(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.prefix = os.path.join(self.tmp.name, "prefix")
        self.patches = os.path.join(self.tmp.name, "patches")
        os.makedirs(os.path.join(self.prefix, os.path.dirname(REL)))
        os.makedirs(self.patches)
        self.target = os.path.join(self.prefix, REL)
        with open(self.target, "w") as f:
            f.write(ORIGINAL)
        with open(os.path.join(self.patches, "guard.diff"), "w") as f:
            f.write(DIFF)
        self.write_series(["guard.diff"])

    def write_series(self, names):
        with open(os.path.join(self.patches, "series"), "w") as f:
            f.write("\n".join(names) + "\n")

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *extra):
        return subprocess.run([sys.executable, "-I", RTBUILD, "patch-dsh", "--prefix", self.prefix,
                               "--patches", self.patches, *extra],
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                              env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})

    def test_parse_hunks(self):
        files = rtbuild.parse_unified_diff(DIFF)
        self.assertEqual([f[0] for f in files], [REL])
        (old1, new1), (old2, new2) = files[0][1]
        self.assertIn("\t\tif (await guard(req, res)) return;\n", new1)
        self.assertNotIn("guard", old1)
        self.assertEqual(new2, "line 40\n-- a removed-looking added line\n")

    def test_record_then_apply_pinned(self):
        r = self.run_cli("--dsh-version", "1.0.0", "--record")
        self.assertEqual(r.returncode, 0, r.stdout)
        pins = load(os.path.join(self.patches, "pins.json"))
        pin = pins["patches"]["guard.diff"]["1.0.0"][REL]
        self.assertEqual(pin["before"], sha(ORIGINAL))
        patched = read(self.target)
        self.assertEqual(pin["after"], sha(patched))
        self.assertIn("guard(req, res)", patched)
        # idempotent on an already patched tree
        r = self.run_cli("--dsh-version", "1.0.0")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("already patched", r.stdout)
        # a fresh original applies to exactly the pinned result, by replacing the file (hard links survive)
        with open(self.target + ".orig", "w") as f:
            f.write(ORIGINAL)
        os.replace(self.target + ".orig", self.target)
        link = self.target + ".link"
        os.link(self.target, link)
        r = self.run_cli("--dsh-version", "1.0.0", "--out", os.path.join(self.tmp.name, "applied.json"))
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(sha(read(self.target)), pin["after"])
        self.assertEqual(read(link), ORIGINAL)
        self.assertEqual(load(os.path.join(self.tmp.name, "applied.json")), ["guard.diff"])

    def record_fixture(self, existing=True):
        with open(self.target, "w") as out:
            out.write("before\n")
        with open(os.path.join(self.patches, "guard.diff"), "w") as out:
            out.write("--- a/%s\n+++ b/%s\n@@ -1,1 +1,1 @@\n-before\n+after\n" % (REL, REL))
        pins = {"schema": 1, "patches": {"guard.diff": {
            "old-%d" % i: {REL: {"before": "1" * 64, "after": "2" * 64}} for i in range(5)
        }}}
        path = os.path.join(self.patches, "pins.json")
        if existing:
            with open(path, "w") as out:
                json.dump(pins, out, indent=2, sort_keys=True)
                out.write("\n")
        return path, pins

    def run_record(self, limit=None, umask=0o022):
        def setup_child():
            os.umask(umask)
            if limit is not None:
                import resource
                import signal
                signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
                resource.setrlimit(resource.RLIMIT_FSIZE, (limit, limit))
        return subprocess.run([sys.executable, "-I", RTBUILD, "patch-dsh", "--prefix", "prefix",
                               "--patches", "patches", "--dsh-version", "new", "--record"],
                              cwd=self.tmp.name, preexec_fn=setup_child, timeout=10,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                              env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})

    @unittest.skipUnless(sys.platform == "linux", "Linux file-size limit")
    def test_record_write_failure_preserves_previous_pins(self):
        path, previous = self.record_fixture()
        before = read(path)
        entries = sorted(os.listdir(self.patches))
        result = self.run_record(limit=256)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("[Errno 27]", result.stdout)
        self.assertEqual(read(self.target), "after\n")  # pins publication does not roll back the prefix
        self.assertEqual(read(path), before)
        self.assertEqual(sorted(os.listdir(self.patches)), entries)
        retry = self.run_record()
        self.assertNotEqual(retry.returncode, 0)
        self.assertIn("matches 0 places", retry.stdout)
        self.assertEqual(read(path), before)
        with open(self.target, "w") as out:
            out.write("before\n")
        recovered = self.run_record()
        self.assertEqual(recovered.returncode, 0, recovered.stdout)
        for version, pin in previous["patches"]["guard.diff"].items():
            self.assertEqual(load(path)["patches"]["guard.diff"][version], pin)

    @unittest.skipUnless(sys.platform == "linux", "Linux file-size limit")
    def test_record_write_failure_does_not_publish_new_pins(self):
        path, _ = self.record_fixture(existing=False)
        entries = sorted(os.listdir(self.patches))
        result = self.run_record(limit=256)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("[Errno 27]", result.stdout)
        self.assertEqual(read(self.target), "after\n")
        self.assertFalse(os.path.lexists(path))
        self.assertEqual(sorted(os.listdir(self.patches)), entries)

    @unittest.skipUnless(sys.platform == "linux", "Linux mode and child umask")
    def test_record_preserves_format_versions_and_existing_mode(self):
        path, expected = self.record_fixture()
        entries = sorted(os.listdir(self.patches))
        os.chmod(path, 0o640)
        result = self.run_record(umask=0o077)
        self.assertEqual(result.returncode, 0, result.stdout)
        expected["patches"]["guard.diff"]["new"] = {REL: {"before": sha("before\n"), "after": sha("after\n")}}
        self.assertEqual(read(path), json.dumps(expected, indent=2, sort_keys=True) + "\n")
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o640)
        self.assertEqual(sorted(os.listdir(self.patches)), entries)

    @unittest.skipUnless(sys.platform == "linux", "Linux mode and child umask")
    def test_record_new_pins_respects_umask(self):
        path, _ = self.record_fixture(existing=False)
        result = self.run_record(umask=0o027)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o640)
        self.assertEqual(load(path)["patches"]["guard.diff"]["new"][REL],
                         {"before": sha("before\n"), "after": sha("after\n")})

    @unittest.skipUnless(sys.platform == "linux", "Linux symlink and hardlink semantics")
    def test_record_links_keep_in_place_semantics(self):
        path = os.path.join(self.patches, "pins.json")
        for kind in ("relative", "dangling", "hard"):
            with self.subTest(kind=kind):
                if os.path.lexists(path):
                    os.unlink(path)
                _, previous = self.record_fixture(existing=kind != "dangling")
                alias = os.path.join(self.patches, kind + "-pins.json")
                if kind == "hard":
                    os.link(path, alias)
                    inode = os.stat(path).st_ino
                else:
                    if kind == "relative":
                        os.replace(path, alias)
                    os.symlink(os.path.basename(alias), path)
                result = self.run_record()
                self.assertEqual(result.returncode, 0, result.stdout)
                if kind == "hard":
                    self.assertEqual(os.stat(path).st_ino, inode)
                    self.assertEqual(os.stat(alias).st_ino, inode)
                else:
                    self.assertTrue(os.path.islink(path))
                    self.assertEqual(os.readlink(path), os.path.basename(alias))
                recorded = load(alias)["patches"]["guard.diff"]
                self.assertEqual(recorded["new"][REL], {"before": sha("before\n"), "after": sha("after\n")})
                if kind != "dangling":
                    for version, pin in previous["patches"]["guard.diff"].items():
                        self.assertEqual(recorded[version], pin)

    @unittest.skipUnless(sys.platform == "linux" and os.geteuid() != 0, "Linux non-root permission check")
    def test_record_readonly_pins_is_not_replaced_via_directory_permission(self):
        path, _ = self.record_fixture()
        before = read(path)
        os.chmod(path, 0o444)
        try:
            result = self.run_record()
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("[Errno 13]", result.stdout)
            self.assertEqual(read(path), before)
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o444)
        finally:
            os.chmod(path, 0o644)

    @unittest.skipUnless(sys.platform == "linux" and os.geteuid() != 0, "Linux non-root permission check")
    def test_record_readonly_parent_keeps_existing_file_writable(self):
        path, _ = self.record_fixture()
        inode = os.stat(path).st_ino
        os.chmod(self.patches, 0o555)
        try:
            result = self.run_record()
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertEqual(os.stat(path).st_ino, inode)
            self.assertIn("new", load(path)["patches"]["guard.diff"])
        finally:
            os.chmod(self.patches, 0o755)

    @unittest.skipUnless(sys.platform == "linux", "Linux hardlink and file-size limit")
    def test_record_link_write_failure_retains_original_nonatomic_behavior(self):
        path, _ = self.record_fixture()
        before = read(path)
        alias = os.path.join(self.patches, "hard-pins.json")
        os.link(path, alias)
        result = self.run_record(limit=256)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("[Errno 27]", result.stdout)
        self.assertEqual(os.stat(path).st_ino, os.stat(alias).st_ino)
        self.assertEqual(len(read(path)), 256)
        self.assertNotEqual(read(path), before)
        self.assertEqual(read(alias), read(path))

    def test_unpinned_version_fails(self):
        self.run_cli("--dsh-version", "1.0.0", "--record")
        with open(self.target, "w") as f:
            f.write(ORIGINAL)
        r = self.run_cli("--dsh-version", "1.0.1")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("no pin for DSH 1.0.1", r.stdout)
        self.assertEqual(read(self.target), ORIGINAL)

    def test_drifted_original_fails(self):
        self.run_cli("--dsh-version", "1.0.0", "--record")
        with open(self.target, "w") as f:
            f.write(ORIGINAL + "// upstream change\n")
        r = self.run_cli("--dsh-version", "1.0.0")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("differs from the vetted", r.stdout)

    def chain_fixture(self, broken=False):
        first = rtbuild.apply_hunks(ORIGINAL, rtbuild.parse_unified_diff(DIFF)[0][1], "first")
        second_diff = "--- a/%s\n+++ b/%s\n@@ -1,1 +1,1 @@\n-line 1\n+later reviewed patch\n" % (REL, REL)
        final = first.replace("line 1\n", "later reviewed patch\n", 1)
        with open(os.path.join(self.patches, "zz-later.diff"), "w") as out:
            out.write(second_diff)
        self.write_series(["guard.diff", "zz-later.diff"])
        pins = {"schema": 1, "patches": {
            "guard.diff": {"1.0.0": {REL: {"before": sha(ORIGINAL), "after": sha(first)}}},
            "zz-later.diff": {"1.0.0": {REL: {"before": "0" * 64 if broken else sha(first), "after": sha(final)}}},
        }}
        with open(os.path.join(self.patches, "pins.json"), "w") as out:
            json.dump(pins, out)
        return first, final

    def test_ordered_hash_chain_is_idempotent_at_final_and_intermediate(self):
        first, final = self.chain_fixture()
        for starting in (ORIGINAL, first, final):
            with open(self.target, "w") as out:
                out.write(starting)
            result = self.run_cli("--dsh-version", "1.0.0")
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertEqual(read(self.target), final)

    def test_broken_hash_chain_fails_before_writing_any_file(self):
        self.chain_fixture(broken=True)
        result = self.run_cli("--dsh-version", "1.0.0")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("broken patch hash chain", result.stdout)
        self.assertEqual(read(self.target), ORIGINAL)

    def test_unknown_hash_is_not_accepted_by_chain_idempotency(self):
        _, final = self.chain_fixture()
        with open(self.target, "w") as out:
            out.write(final + "unexpected modification\n")
        result = self.run_cli("--dsh-version", "1.0.0")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("differs from the vetted", result.stdout)

    def test_series_controls_order_even_when_filenames_disagree(self):
        _, final = self.chain_fixture()
        os.rename(os.path.join(self.patches, "zz-later.diff"),
                  os.path.join(self.patches, "aa-later.diff"))
        pins_path = os.path.join(self.patches, "pins.json")
        pins = load(pins_path)
        pins["patches"]["aa-later.diff"] = pins["patches"].pop("zz-later.diff")
        with open(pins_path, "w") as f:
            json.dump(pins, f)
        self.write_series(["# Explicit order", "guard.diff", "", "aa-later.diff"])
        applied = os.path.join(self.tmp.name, "applied.json")
        result = self.run_cli("--dsh-version", "1.0.0", "--out", applied)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(read(self.target), final)
        self.assertEqual(load(applied), ["guard.diff", "aa-later.diff"])

    def test_invalid_series_fails_before_recording_or_writing(self):
        for names in ([], ["guard.diff", "guard.diff"], ["missing.diff"],
                      ["guard.diff", "missing.diff"]):
            with self.subTest(names=names):
                with open(self.target, "w") as f:
                    f.write(ORIGINAL)
                pins_path = os.path.join(self.patches, "pins.json")
                if os.path.exists(pins_path):
                    os.remove(pins_path)
                self.write_series(names)
                result = self.run_cli("--dsh-version", "1.0.0", "--record")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("series", result.stdout)
                self.assertEqual(read(self.target), ORIGINAL)
                self.assertFalse(os.path.exists(os.path.join(self.patches, "pins.json")))

    def test_unlisted_patch_fails_before_writing(self):
        self.chain_fixture()
        self.write_series(["guard.diff"])
        result = self.run_cli("--dsh-version", "1.0.0")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("zz-later.diff", result.stdout)
        self.assertEqual(read(self.target), ORIGINAL)

    def test_missing_series_has_no_filename_order_fallback(self):
        os.remove(os.path.join(self.patches, "series"))
        result = self.run_cli("--dsh-version", "1.0.0", "--record")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("series", result.stdout)
        self.assertEqual(read(self.target), ORIGINAL)

    def test_deletion_generator_replays_only_the_explicit_prefix(self):
        path = os.path.join(HERE, "..", "session-deletion", "generate.py")
        spec = importlib.util.spec_from_file_location("deletion_generate", path)
        generator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(generator)
        with open(self.target, "w") as f:
            f.write("original\n")
        package = os.path.join(self.prefix, "node_modules/@x/web/package.json")
        with open(package, "w") as f:
            json.dump({"version": "0.2.2-rc.1"}, f)
        os.remove(os.path.join(self.patches, "guard.diff"))
        self.write_series(["zzzzzzz-prior.diff", generator.PATCH_NAME, "aa-after.diff"])
        for name, before, after in [("zzzzzzz-prior.diff", "original", "preceding"),
                                   (generator.PATCH_NAME, "preceding", "generated"),
                                   ("aa-after.diff", "generated", "later")]:
            with open(os.path.join(self.patches, name), "w") as f:
                f.write("--- a/%s\n+++ b/%s\n@@ -1 +1 @@\n-%s\n+%s\n"
                        % (REL, REL, before, after))
        with mock.patch.object(generator, "RUNTIME", Path(self.tmp.name)), \
                mock.patch.object(generator, "transforms", return_value={REL: [("preceding", "generated")]}):
            self.assertEqual(generator.build(Path(self.prefix)),
                             {REL: ("preceding\n", "generated\n")})
        self.assertEqual(read(self.target), "original\n")

    def test_record_refuses_ambiguous_or_missing_context(self):
        with open(self.target, "w") as f:
            f.write(ORIGINAL + ORIGINAL)          # every hunk matches twice
        r = self.run_cli("--dsh-version", "2.0.0", "--record")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("matches 2 places", r.stdout)
        with open(self.target, "w") as f:
            f.write(ORIGINAL.replace("await route.handler", "await route.run"))
        r = self.run_cli("--dsh-version", "2.0.0", "--record")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("matches 0 places", r.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.patches, "pins.json")))

    def test_shipped_patch_parses_against_pins(self):
        shipped = os.path.join(HERE, "..", "patches")
        pins = load(os.path.join(shipped, "pins.json"))
        for name in rtbuild.patch_names(shipped):
            files = rtbuild.parse_unified_diff(read(os.path.join(shipped, name)))
            self.assertTrue(files and all(hunks for _, hunks in files), name)
            for version, by_file in pins["patches"][name].items():
                self.assertEqual(sorted(by_file), sorted(rel for rel, _ in files), (name, version))

    @unittest.skipUnless(os.environ.get("DSH_FS_LOCAL_ORIGINAL_JS"),
                         "set DSH_FS_LOCAL_ORIGINAL_JS to the unpatched npm 0.2.1-rc.2 lib/index.js")
    def test_shipped_exclusive_create_patch_applies_and_rejects_drift(self):
        shipped = os.path.join(HERE, "..", "patches")
        name = "fs-create-exclusive.diff"
        rel = "node_modules/@deepseek-ai/dsh-fs-local/lib/index.js"
        pins = load(os.path.join(shipped, "pins.json"))
        pin = pins["patches"][name]["0.2.2-rc.1"][rel]
        with open(os.environ["DSH_FS_LOCAL_ORIGINAL_JS"], "rb") as source:
            original = source.read()
        self.assertEqual(hashlib.sha256(original).hexdigest(), pin["before"])
        target = os.path.join(self.prefix, rel)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as out:
            out.write(original)
        os.remove(os.path.join(self.patches, "guard.diff"))
        with open(os.path.join(self.patches, name), "w") as out:
            out.write(read(os.path.join(shipped, name)))
        self.write_series([name])
        with open(os.path.join(self.patches, "pins.json"), "w") as out:
            json.dump({"schema": 1, "patches": {name: pins["patches"][name]}}, out)

        result = self.run_cli("--dsh-version", "0.2.2-rc.1")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(sha(read(target)), pin["after"])
        again = self.run_cli("--dsh-version", "0.2.2-rc.1")
        self.assertEqual(again.returncode, 0, again.stdout)
        self.assertIn("already patched", again.stdout)
        with open(target, "wb") as out:
            out.write(original + b"// upstream drift\n")
        changed = self.run_cli("--dsh-version", "0.2.2-rc.1")
        self.assertNotEqual(changed.returncode, 0)
        self.assertIn("differs from the vetted", changed.stdout)
        with open(target, "rb") as source:
            self.assertEqual(source.read(), original + b"// upstream drift\n")

    def three_stage_chain_fixture(self, broken=False):
        first, second = self.chain_fixture(broken=broken)
        final = second.replace("later reviewed patch\n", "final reviewed patch\n", 1)
        name = "zzz-final.diff"
        with open(os.path.join(self.patches, name), "w") as out:
            out.write("--- a/%s\n+++ b/%s\n@@ -1,1 +1,1 @@\n-later reviewed patch\n+final reviewed patch\n" % (REL, REL))
        pins_path = os.path.join(self.patches, "pins.json")
        pins = load(pins_path)
        pins["patches"][name] = {"1.0.0": {REL: {"before": sha(second), "after": sha(final)}}}
        with open(pins_path, "w") as out:
            json.dump(pins, out)
        # Also keep the fixture usable with the explicit-series follow-up.
        with open(os.path.join(self.patches, "series"), "w") as out:
            out.write("guard.diff\nzz-later.diff\nzzz-final.diff\n")
        return first, second, final

    def test_three_stage_chain_resumes_from_every_known_state(self):
        first, second, final = self.three_stage_chain_fixture()
        for stage, starting in enumerate((ORIGINAL, first, second, final)):
            with self.subTest(stage=stage):
                with open(self.target, "w") as out:
                    out.write(starting)
                result = self.run_cli("--dsh-version", "1.0.0")
                self.assertEqual(result.returncode, 0, result.stdout)
                self.assertEqual(read(self.target), final)

    def test_three_stage_chain_rejects_unknown_intermediate_bytes(self):
        _, second, _ = self.three_stage_chain_fixture()
        unknown = second + "unexpected modification\n"
        with open(self.target, "w") as out:
            out.write(unknown)
        result = self.run_cli("--dsh-version", "1.0.0")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("differs from the vetted", result.stdout)
        self.assertEqual(read(self.target), unknown)

    def test_three_stage_broken_chain_fails_before_writing(self):
        self.three_stage_chain_fixture(broken=True)
        result = self.run_cli("--dsh-version", "1.0.0")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("broken patch hash chain", result.stdout)
        self.assertEqual(read(self.target), ORIGINAL)


if __name__ == "__main__":
    unittest.main(verbosity=2)
