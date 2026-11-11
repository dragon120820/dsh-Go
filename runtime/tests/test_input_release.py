"""Local release tests: synthetic archives/materials only; no upstream builds."""
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch


REPO = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("input_store", REPO / "runtime/lib/input_store.py")
store = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(store)


class InputReleaseTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="input-release-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.cas = self.root / "cas"
        self.output = self.root / "release"
        self.objects = []
        self.originals = []
        for abi in ("fixture-arm64", "fixture-x64"):
            self.add_input("proot_1.0_" + abi + ".deb", ("private binary " + abi).encode(),
                           {"kind": "termux_jni", "name": "proot_1.0_" + abi + ".deb",
                            "license": None, "source": "synthetic PIN", "abi": abi})
        self.add_input("fixture-npm.tgz", b"private npm fixture, not an upstream package", {
            "kind": "npm", "name": "fixture-npm", "version": "1.0.0", "license": "MIT",
            "source": "synthetic package-lock.json", "optional": True}, algorithm="sha512")
        self.plan = store.release_plan(self.objects)
        self.source = self.file("upstream-source.tar", b"private corresponding source fixture")
        self.build = self.file("build-materials.tar", b"private build recipe and patches fixture")
        self.plan["artifacts"] = [self.artifact(self.source), self.artifact(self.build)]
        self.plan["requirements"][0]["coverage"] = {
            "license": "GPL-2.0-only", "sources": [self.key(self.source)],
            "build": [self.key(self.build)],
            "evidence": "Synthetic two-ABI example only; source/recipe contain no upstream material.",
        }
        self.plan_path = self.file("source-plan.json", store.json_bytes(self.plan))

    def file(self, name, payload):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return path

    def key(self, path):
        return "sha256-" + hashlib.sha256(path.read_bytes()).hexdigest()

    def artifact(self, path):
        return {"name": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "size": path.stat().st_size, "urls": [],
                "origin": "Private test fixture, not a real source/release provenance."}

    def add_input(self, name, payload, ref, algorithm="sha256"):
        path = self.file(name, payload)
        digest = hashlib.new(algorithm, payload).hexdigest()
        obj = {"key": algorithm + "-" + digest, "algorithm": algorithm, "digest": digest,
               "size": len(payload), "urls": [path.as_uri()], "refs": [ref]}
        self.objects.append(obj)
        self.objects.sort(key=lambda row: row["key"])
        self.originals.append(path)
        return obj

    def prepare(self):
        return store.prepare_release(self.objects, self.plan, self.cas, self.output,
                                     import_paths=[self.source, self.build])

    def test_current_plan_automates_lock_data_and_groups_only_actual_gaps(self):
        objects = store.inventory(REPO)
        plan = store.release_plan(objects)
        self.assertEqual(len(objects), 723)
        required = {key for row in plan["requirements"] for key in row["inputs"]}
        self.assertEqual(len(required), 82)  # 62 non-npm + 20 source-obligation declarations.
        self.assertEqual(len(plan["requirements"]), 54)  # Same package's ABIs share a row.
        self.assertEqual(len(objects) - len(required), 641)
        self.assertEqual(plan["artifacts"], [])
        self.assertTrue(all(row["coverage"] is None for row in plan["requirements"]))
        proot = next(row for row in plan["requirements"] if row["id"] == "termux_jni:proot")
        self.assertEqual(len(proot["source_candidates"]), 1)
        self.assertIn(proot["source_candidates"][0], {obj["key"] for obj in objects})
        self.assertTrue(any("LGPL-3.0-or-later" in str(row["refs"]) for row in plan["requirements"]))

    def test_one_prepare_command_collects_all_bytes_and_offline_check_needs_no_network(self):
        output = io.StringIO()
        with patch.object(store, "inventory", return_value=self.objects), \
                patch.object(store.subprocess, "run") as run, contextlib.redirect_stdout(output):
            code = store.main(["prepare-release", "--plan", str(self.plan_path), "--store", str(self.cas),
                               "--output", str(self.output), "--import-file", str(self.source),
                               "--import-file", str(self.build)])
        self.assertEqual(code, 0, output.getvalue())
        result = json.loads(output.getvalue())
        self.assertEqual(result["status"], "VERIFIED_DECLARED_MATERIALS")
        self.assertEqual((result["input_objects"], result["material_objects"], result["upload_files"]), (3, 2, 7))
        run.assert_not_called()
        expected = {obj["key"] for obj in self.objects} | {self.key(self.source), self.key(self.build)}
        self.assertEqual({path.name for path in self.output.iterdir()},
                         expected | {store.RELEASE_MANIFEST, "SHA256SUMS"})
        manifest = json.loads((self.output / store.RELEASE_MANIFEST).read_text(encoding="utf-8"))
        self.assertEqual(manifest["inventory"], self.objects)
        self.assertEqual(manifest["source_plan"], self.plan)
        self.assertEqual(result["bytes"], sum((self.output / name).stat().st_size for name in expected))
        with patch.object(store.urllib.request, "urlopen", side_effect=AssertionError("must stay offline")):
            self.assertEqual(store.check_release(self.objects, self.plan, self.output), result)
            checked = io.StringIO()
            with patch.object(store, "inventory", return_value=self.objects), contextlib.redirect_stdout(checked):
                code = store.main(["check-release", "--plan", str(self.plan_path), "--output", str(self.output)])
            self.assertEqual(code, 0, checked.getvalue())
            self.assertEqual(json.loads(checked.getvalue()), result)
        for path in self.originals + [self.source, self.build]:
            matches = [name for name in expected if (self.output / name).read_bytes() == path.read_bytes()]
            self.assertEqual(len(matches), 1)
        for name in expected:
            self.assertNotEqual((self.output / name).stat().st_ino, (self.cas / name).stat().st_ino)
        check = subprocess.run(["sha256sum", "-c", "SHA256SUMS"], cwd=self.output,
                               capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)

    def test_failed_or_stale_plan_never_fetches_or_creates_release(self):
        variants = []
        pending = copy.deepcopy(self.plan)
        pending["requirements"][0]["coverage"] = None
        variants.append(pending)
        removed = copy.deepcopy(self.plan)
        removed["requirements"].clear()
        variants.append(removed)
        stale = copy.deepcopy(self.plan)
        stale["inventory_sha256"] = "0" * 64
        variants.append(stale)
        renamed = copy.deepcopy(self.plan)
        renamed["requirements"][0]["inputs"] = []
        variants.append(renamed)
        for plan in variants:
            with self.subTest(plan=plan), patch.object(store.urllib.request, "urlopen") as opener:
                with self.assertRaises(store.InputError):
                    store.prepare_release(self.objects, plan, self.cas, self.output)
                opener.assert_not_called()
                self.assertFalse(self.cas.exists())
                self.assertFalse(self.output.exists())

    def test_copyleft_needs_both_sources_and_build_materials_and_cannot_be_downgraded(self):
        for missing in ("sources", "build"):
            plan = copy.deepcopy(self.plan)
            plan["requirements"][0]["coverage"][missing] = []
            with self.subTest(missing=missing), self.assertRaisesRegex(store.InputError, "corresponding " + missing):
                store.release_objects(self.objects, plan)
        obj = self.add_input("lgpl.tgz", b"private native LGPL package", {
            "kind": "npm", "name": "fixture-native", "version": "1.0.0", "license": "LGPL-3.0-or-later",
            "source": "synthetic lock"})
        plan = store.release_plan([obj])
        plan["requirements"][0]["coverage"] = {
            "license": "MIT", "sources": [], "build": [], "evidence": "incorrect downgrade"}
        with self.assertRaisesRegex(store.InputError, "corresponding sources"):
            store.release_objects([obj], plan)

    def test_resolved_permissive_unknown_needs_no_fabricated_source_artifact(self):
        plan = copy.deepcopy(self.plan)
        plan["artifacts"] = []
        plan["requirements"][0]["coverage"] = {
            "license": "MIT", "sources": [], "build": [],
            "evidence": "Private permissive binary fixture; actual license reviewed in its archive."}
        self.assertEqual(store.release_objects(self.objects, plan), self.objects)
        for value in ("MIT GPL-2.0-only", "(MIT", "MIT OR", "", None):
            self.assertFalse(store.source_optional(value))

    def test_explicit_review_can_cover_a_bundled_component_absent_from_lock_license(self):
        npm = next(obj for obj in self.objects if obj["algorithm"] == "sha512")
        plan = store.release_plan(self.objects, require_source=[npm["key"]])
        plan["artifacts"] = self.plan["artifacts"]
        for row in plan["requirements"]:
            row["coverage"] = copy.deepcopy(self.plan["requirements"][0]["coverage"])
        self.assertEqual(len(plan["requirements"]), 2)
        self.assertEqual(len(store.release_objects(self.objects, plan)), 5)
        with self.assertRaisesRegex(store.InputError, "unknown inputs"):
            store.release_plan(self.objects, require_source=["sha256-unknown"])

    def test_shared_object_with_different_lock_license_hints_keeps_both_references(self):
        obj = copy.deepcopy(next(obj for obj in self.objects if obj["algorithm"] == "sha512"))
        alternate = {**obj["refs"][0], "license": "LGPL-3.0-or-later", "source": "another fixture lock"}
        obj["refs"].append(alternate)
        plan = store.release_plan([obj])
        self.assertEqual(plan["requirements"][0]["refs"], obj["refs"])
        plan["artifacts"] = self.plan["artifacts"]
        plan["requirements"][0]["coverage"] = self.plan["requirements"][0]["coverage"]
        self.assertEqual(len(store.release_objects([obj], plan)), 3)

    def test_wrong_material_hash_stops_without_a_publishable_release(self):
        self.source.write_bytes(b"corrupted private source")
        result = self.prepare()
        self.assertTrue(result["errors"])
        self.assertFalse(self.output.exists())
        self.assertFalse((self.cas / ("sha256-" + self.plan["artifacts"][0]["sha256"])).exists())

    def test_explicit_source_url_collects_verified_bytes_without_executing_them(self):
        plan = copy.deepcopy(self.plan)
        for artifact in plan["artifacts"]:
            artifact["urls"] = ["https://example.invalid/" + artifact["name"]]
        payloads = {"https://example.invalid/" + path.name: path.read_bytes() for path in (self.source, self.build)}
        opener = store.urllib.request.urlopen

        def open_source(request, **kwargs):
            if request.full_url in payloads:
                return io.BytesIO(payloads[request.full_url])
            return opener(request, **kwargs)

        with patch.object(store.urllib.request, "urlopen", side_effect=open_source), \
                patch.object(store.subprocess, "run") as run:
            result = store.prepare_release(self.objects, plan, self.cas, self.output)
        self.assertFalse(result["errors"])
        run.assert_not_called()
        self.assertEqual((self.output / self.key(self.source)).read_bytes(), self.source.read_bytes())

    def test_material_download_bad_size_stops_before_later_sources(self):
        plan = copy.deepcopy(self.plan)
        for artifact in plan["artifacts"]:
            artifact["urls"] = ["https://example.invalid/first", "https://example.invalid/second"]
        # Keep the original inputs cached; the only opening is a source material.
        imported = store.import_files(self.objects, self.originals, self.cas)
        self.assertFalse(imported["errors"])
        with patch.object(store.urllib.request, "urlopen", return_value=io.BytesIO(b"x" * 200)) as opener:
            result = store.prepare_release(self.objects, plan, self.cas, self.output)
        self.assertTrue(result["stopped"])
        self.assertIn("size exceeds", result["errors"][0]["error"])
        self.assertEqual(opener.call_count, 1)
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.cas.glob(".part-*")))

    def test_source_material_schema_rejects_unpinned_unreferenced_and_private_urls(self):
        for field, value in (("sha256", ""), ("size", -1), ("size", True),
                             ("name", "../outside"), ("origin", ""),
                             ("urls", ["file:///private/source"]),
                             ("urls", ["https://example.invalid/source?token=private"]),
                             ("urls", ["https://user:pass@example.invalid/source"])):
            plan = copy.deepcopy(self.plan)
            plan["artifacts"][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(store.InputError):
                store.release_objects(self.objects, plan)
        plan = copy.deepcopy(self.plan)
        plan["artifacts"].append(self.artifact(self.file("unrelated", b"unrelated private data")))
        with self.assertRaisesRegex(store.InputError, "unreferenced"):
            store.release_objects(self.objects, plan)
        plan = copy.deepcopy(self.plan)
        plan["requirements"][0]["coverage"]["sources"] = ["../../untrusted"]
        with self.assertRaisesRegex(store.InputError, "unknown material"):
            store.release_objects(self.objects, plan)

    def test_release_detects_missing_extra_tampered_manifest_and_changed_plan(self):
        self.prepare()
        path = self.output / self.key(self.source)
        good = path.read_bytes()
        path.unlink()
        with self.assertRaisesRegex(store.InputError, "file set"):
            store.check_release(self.objects, self.plan, self.output)
        path.write_bytes(good)
        extra = self.output / "unexpected-file"
        extra.write_bytes(b"not declared")
        with self.assertRaisesRegex(store.InputError, "file set"):
            store.check_release(self.objects, self.plan, self.output)
        extra.unlink()
        path.write_bytes(good[:-1] + b"!")
        with self.assertRaisesRegex(store.InputError, "checksum"):
            store.check_release(self.objects, self.plan, self.output)
        path.write_bytes(good)
        metadata = self.output / store.RELEASE_MANIFEST
        before = metadata.read_bytes()
        tampered = json.loads(before)
        tampered["source_plan"]["requirements"][0]["coverage"]["evidence"] = "not the owner's plan"
        metadata.write_bytes(store.json_bytes(tampered))
        with self.assertRaisesRegex(store.InputError, "manifest differs"):
            store.check_release(self.objects, self.plan, self.output)
        metadata.write_bytes(before)
        changed = copy.deepcopy(self.plan)
        changed["requirements"][0]["coverage"]["evidence"] = "a different retained plan"
        with self.assertRaisesRegex(store.InputError, "manifest differs"):
            store.check_release(self.objects, changed, self.output)
        sums = self.output / "SHA256SUMS"
        sums.write_bytes(sums.read_bytes().replace(b"\n", b"\r\n"))
        with self.assertRaisesRegex(store.InputError, "SHA256SUMS"):
            store.check_release(self.objects, self.plan, self.output)

    def test_regular_files_only_and_existing_output_remains_untouched(self):
        self.prepare()
        before = {path.name: path.read_bytes() for path in self.output.iterdir()}
        with self.assertRaisesRegex(store.InputError, "already exist"):
            self.prepare()
        self.assertEqual(before, {path.name: path.read_bytes() for path in self.output.iterdir()})
        path = self.output / self.key(self.source)
        path.unlink()
        path.symlink_to(self.cas / path.name)
        with self.assertRaisesRegex(store.InputError, "regular file"):
            store.check_release(self.objects, self.plan, self.output)
        path.unlink()
        path.mkdir()
        with self.assertRaisesRegex(store.InputError, "regular file"):
            store.check_release(self.objects, self.plan, self.output)

    def test_copy_failure_cleans_only_new_output_and_preserves_cache_and_other_directory(self):
        store.import_files(self.objects, self.originals, self.cas)
        materials = store.release_objects(self.objects, self.plan)
        store.import_files(materials, [self.source, self.build], self.cas)
        unrelated = self.file("other/keep", b"leave this file alone")
        before = {path.name: path.read_bytes() for path in self.cas.iterdir()}
        with patch.object(store, "store_stream", side_effect=OSError("synthetic copy failure")):
            with self.assertRaisesRegex(OSError, "copy failure"):
                store.prepare_release(self.objects, self.plan, self.cas, self.output)
        self.assertFalse(self.output.exists())
        self.assertEqual(before, {path.name: path.read_bytes() for path in self.cas.iterdir()})
        self.assertEqual(unrelated.read_bytes(), b"leave this file alone")

    @unittest.skipIf(os.geteuid() == 0, "permission denial must be checked as a non-root user")
    def test_unreadable_import_returns_error_without_release_or_original_file_changes(self):
        original_bytes = self.source.read_bytes()
        self.source.chmod(0)
        try:
            result = self.prepare()
            self.assertTrue(result["errors"])
            self.assertIn("Permission denied", result["errors"][0]["error"])
            self.assertFalse(self.output.exists())
        finally:
            self.source.chmod(0o600)
        self.assertEqual(self.source.read_bytes(), original_bytes)

    def test_repeated_prepare_is_deterministic_and_copies_are_independent_of_cache(self):
        self.prepare()
        second = self.root / "release-two"
        result = store.prepare_release(self.objects, self.plan, self.cas, second)
        self.assertFalse(result["errors"])
        self.assertEqual({path.name: path.read_bytes() for path in self.output.iterdir()},
                         {path.name: path.read_bytes() for path in second.iterdir()})
        key = self.key(self.source)
        (self.cas / key).write_bytes(b"corrupted later cache")
        self.assertEqual((self.output / key).read_bytes(), self.source.read_bytes())
        self.assertFalse(store.check_release(self.objects, self.plan, self.output)["errors"])

    def test_cached_npm_source_candidates_are_hints_only_and_never_execute_scripts(self):
        payload = io.BytesIO()
        package = {"name": "fixture-native", "version": "1.0.0", "license": "LGPL-3.0-or-later",
                   "repository": {"type": "git", "url": "https://example.invalid/source.git"},
                   "gitHead": "private-fixture-revision", "scripts": {"install": "exit 91"}}
        with tarfile.open(fileobj=payload, mode="w:gz") as archive:
            raw = json.dumps(package).encode("utf-8")
            info = tarfile.TarInfo("package/package.json")
            info.size = len(raw)
            archive.addfile(info, io.BytesIO(raw))
        obj = self.add_input("native.tgz", payload.getvalue(), {
            "kind": "npm", "name": "fixture-native", "version": "1.0.0",
            "license": "LGPL-3.0-or-later", "source": "synthetic lock"})
        self.file("cas/" + obj["key"], payload.getvalue())
        with patch.object(store.subprocess, "run") as run, patch.object(store.urllib.request, "urlopen") as opener:
            plan = store.release_plan([obj], self.cas)
        run.assert_not_called()
        opener.assert_not_called()
        self.assertIsNone(plan["requirements"][0]["coverage"])
        self.assertEqual(plan["source_hints"][obj["key"]]["gitHead"], package["gitHead"])
        self.assertNotIn("scripts", plan["source_hints"][obj["key"]])
        self.assertEqual(plan["artifacts"], [])
        (self.cas / obj["key"]).write_bytes(b"not the pinned tarball")
        with self.assertRaisesRegex(store.InputError, "checksum"):
            store.release_plan([obj], self.cas)

    def test_release_metadata_rejects_duplicate_fields_and_cli_reports_errors_as_json(self):
        self.plan_path.write_text('{"schema":1,"schema":1}', encoding="utf-8")
        output = io.StringIO()
        with patch.object(store, "inventory", return_value=self.objects), contextlib.redirect_stdout(output):
            code = store.main(["prepare-release", "--plan", str(self.plan_path),
                               "--store", str(self.cas), "--output", str(self.output)])
        self.assertEqual(code, 1)
        self.assertIn("duplicate JSON", json.loads(output.getvalue())["errors"][0]["error"])
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
