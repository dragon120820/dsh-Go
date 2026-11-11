#!/usr/bin/env python3
"""Keep checksum-addressed runtime originals; never publish or execute their contents."""
import argparse
import base64
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request


MAX_UNKNOWN_BYTES = 1024 * 1024 * 1024


class InputError(Exception):
    pass


class TransportError(InputError):
    pass


def shell_inputs(repo):
    source = (repo / "runtime/build-runtime.sh").read_text(encoding="utf-8")
    values = {}
    for name, value in re.findall(r'^([A-Z][A-Z0-9_]*)="([^"\n]*)"', source, re.M):
        default = re.fullmatch(r"\$\{" + name + r":-(.*)\}", value)
        values[name] = default[1] if default else value
    block = re.search(r"^declare -A PIN=\(\n(.*?)^\)", source, re.M | re.S)
    if block is None:
        raise InputError("build-runtime.sh has no supported PIN table")
    pins = {}
    for line in block[1].splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = re.fullmatch(r"\s*\[([^\]]+)\]=([0-9a-f]{64})(?:\s+#.*)?\s*", line)
        if match is None:
            raise InputError("unsupported/unparsed PIN entry in build-runtime.sh")
        pins[match[1]] = match[2]
    return values, pins


def add_object(objects, algorithm, digest, urls, ref, size=None):
    if algorithm not in ("sha256", "sha512") or not re.fullmatch(
            "[0-9a-f]{" + str(hashlib.new(algorithm).digest_size * 2) + "}", digest):
        raise InputError("unsupported or malformed input digest")
    key = algorithm + "-" + digest
    row = objects.setdefault(key, {"key": key, "algorithm": algorithm, "digest": digest,
                                   "size": size, "urls": [], "refs": []})
    if size is not None:
        if row["size"] is not None and row["size"] != size:
            raise InputError("conflicting sizes for " + key)
        row["size"] = size
    for url in urls:
        if url not in row["urls"]:
            row["urls"].append(url)
    row["refs"].append(ref)


def lock_label(lock, repo):
    try:
        return str(lock.relative_to(repo))
    except ValueError:
        return str(lock)


def add_npm_lock(objects, lock, repo):
    data = json.loads(lock.read_text(encoding="utf-8"))
    for package_path, package in data["packages"].items():
        if not package_path:
            continue
        if package.get("link"):
            raise InputError("local linked package has no original: " + package_path)
        sri = package.get("integrity", "")
        match = re.fullmatch(r"(sha256|sha512)-([A-Za-z0-9+/]+={0,2})", sri)
        if match is None or not package.get("resolved"):
            raise InputError("missing/unsupported resolved or integrity: " + package_path)
        digest = base64.b64decode(match[2], validate=True).hex()
        add_object(objects, match[1], digest, [package["resolved"]], {
            "kind": "npm", "source": lock_label(lock, repo), "package_path": package_path,
            "name": package_path.rsplit("node_modules/", 1)[-1], "version": package["version"],
            "integrity": sri, "license": package.get("license"),
            "optional": bool(package.get("optional")), "os": package.get("os"),
            "cpu": package.get("cpu"), "libc": package.get("libc"),
        })


def npm_inventory(locks, repo):
    objects = {}
    for lock in locks:
        add_npm_lock(objects, lock, repo)
    return [objects[key] for key in sorted(objects)]


def inventory(repo, npm_locks=None, all_locks=False):
    values, pins = shell_inputs(repo)
    objects = {}
    used_pins = set()

    def pinned(name, urls, abi, role):
        if name not in pins:
            raise InputError("default input has no PIN: " + name)
        used_pins.add(name)
        add_object(objects, "sha256", pins[name], urls, {
            "kind": role, "name": name, "abi": abi,
            "source": "runtime/build-runtime.sh:PIN[" + name + "]",
            "license": None, "license_note": "inspect original copyright/COPYING and corresponding source before redistribution",
        })

    for abi, ubuntu, node, termux in (("arm64-v8a", "arm64", "arm64", "aarch64"),
                                     ("x86_64", "amd64", "x64", "x86_64")):
        name = "ubuntu-base-" + values["UBUNTU_VERSION"] + "-base-" + ubuntu + ".tar.gz"
        pinned(name, [values["UBUNTU_MIRROR"] + "/" + name], abi, "ubuntu_base")
        name = "node-v" + values["NODE_VERSION"] + "-linux-" + node + ".tar.xz"
        pinned(name, [values["NODE_MIRROR"] + "/v" + values["NODE_VERSION"] + "/" + name],
               abi, "node_guest_and_x64_build_host" if node == "x64" else "node_guest")
        name = "cpython-" + values["PYTHON_VERSION"] + "+" + values["PBS_RELEASE"] + "-" + termux + "-unknown-linux-gnu-install_only_stripped.tar.gz"
        pinned(name, [values[key] + "/" + values["PBS_RELEASE"] + "/" + name
                      for key in ("PBS_MIRROR", "PBS_OFFICIAL")], abi, "python")
        for package, version_key, pool in (("proot", "PROOT_VERSION", "p"),
                                           ("libtalloc", "TALLOC_VERSION", "libt"),
                                           ("libandroid-shmem", "SHMEM_VERSION", "liba")):
            name = package + "_" + values[version_key] + "_" + termux + ".deb"
            pinned(name, [values["TERMUX_REPO"] + "/pool/main/" + pool + "/" + package + "/" + name], abi, "termux_jni")
        lock = repo / ("runtime/locks/ubuntu-noble-" + ubuntu + ".json")
        mirror = values["UBUNTU_ARCHIVE_" + ("AMD64" if ubuntu == "amd64" else "ARM64")]
        for index, package in enumerate(json.loads(lock.read_text(encoding="utf-8"))["packages"]):
            name = Path(package["filename"]).name
            urls = [mirror + "/" + package["filename"]]
            if "UBUNTU_DEB_FALLBACK" in values:
                urls.append(values["UBUNTU_DEB_FALLBACK"] + "/" + name)
            add_object(objects, "sha256", package["sha256"], urls, {
                "kind": "ubuntu_deb", "name": name, "version": package["version"], "abi": abi,
                "source": lock_label(lock, repo) + ":packages[" + str(index) + "]",
                "license": None, "license_note": "inspect archive copyright and corresponding source before redistribution",
            }, package["size"])
    name = "proot-v" + values["PROOT_VERSION"] + ".zip"
    pinned(name, ["https://github.com/termux/proot/archive/v" + values["PROOT_VERSION"] + ".zip"],
           "both", "proot_source_for_COPYING")
    uncovered = sorted(set(pins) - used_pins)
    if uncovered:
        raise InputError("unsupported/uncovered PIN entries: " + ", ".join(uncovered))
    if npm_locks:
        locks = npm_locks
    else:
        defaults = [repo / ("runtime/locks/" + name + "/package-lock.json") for name in
                    ("dsh-" + values["DSH_VERSION"], "pnpm-" + values["PNPM_VERSION"])]
        pnpm = json.loads(defaults[1].read_text(encoding="utf-8"))["packages"].get("node_modules/pnpm", {})
        if pnpm.get("version") != values["PNPM_VERSION"] or pnpm.get("integrity") != values["PNPM_INTEGRITY"]:
            raise InputError("current pnpm lock version/integrity does not match source pins")
        locks = sorted(set(defaults) | set((repo / "runtime/locks").glob("*/package-lock.json"))) if all_locks else defaults
    for lock in locks:
        add_npm_lock(objects, lock, repo)
    return [objects[key] for key in sorted(objects)]


def digest_file(path, algorithm):
    with path.open("rb") as source:
        return hashlib.file_digest(source, algorithm).hexdigest()


def verify_file(path, obj):
    if digest_file(path, obj["algorithm"]) != obj["digest"]:
        raise InputError("checksum mismatch: " + obj["key"])
    if obj["size"] is not None and path.stat().st_size != obj["size"]:
        raise InputError("size mismatch: " + obj["key"])


def store_stream(source, obj, store):
    """Only a fully verified temporary file may replace the flat object name."""
    store.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix=".part-", dir=store, delete=False) as output:
            temporary = Path(output.name)
            digest = hashlib.new(obj["algorithm"])
            size = 0
            limit = obj["size"] if obj["size"] is not None else MAX_UNKNOWN_BYTES
            while True:
                try:
                    chunk = source.read(min(1024 * 1024, limit - size + 1))
                except (OSError, http.client.HTTPException) as error:
                    raise TransportError("input read failed: " + type(error).__name__) from error
                if not chunk:
                    break
                size += len(chunk)
                if size > limit:
                    reason = "size exceeds expected bytes" if obj["size"] is not None else "input exceeds transfer limit"
                    raise InputError(reason + ": " + obj["key"])
                output.write(chunk)
                digest.update(chunk)
            if digest.hexdigest() != obj["digest"]:
                raise InputError("checksum mismatch: " + obj["key"])
            if obj["size"] is not None and size != obj["size"]:
                raise InputError("size mismatch: " + obj["key"])
            output.flush()
            os.fsync(output.fileno())
        destination = store / obj["key"]
        os.replace(temporary, destination)
        return destination
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def checked_url(url, base=False):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ("https", "file") or parsed.username or parsed.password or parsed.fragment:
        raise InputError("input URL must be HTTPS or local file:// without credentials/fragment")
    if parsed.scheme == "https" and not parsed.netloc:
        raise InputError("HTTPS input URL needs a host")
    if parsed.scheme == "file" and (parsed.netloc not in ("", "localhost") or not parsed.path.startswith("/")):
        raise InputError("file:// input URL must name a local absolute path")
    if base and parsed.query:
        raise InputError("mirror base must not contain a query")
    return url.rstrip("/") + "/" if base else url


def collect_one(obj, store, mirror_base=None):
    destination = store / obj["key"]
    if destination.exists():
        verify_file(destination, obj)
        return destination, "cached"
    urls = list(obj["urls"])
    if mirror_base:
        urls.append(checked_url(mirror_base, base=True) + obj["key"])
    failures = []
    for url in urls:
        request = urllib.request.Request(checked_url(url), headers={"User-Agent": "dsh-go-input-store/1"})
        try:
            response = urllib.request.urlopen(request, timeout=30)
        except (OSError, urllib.error.URLError, http.client.HTTPException) as error:
            failures.append(type(error).__name__)
            continue
        with response:
            try:
                return store_stream(response, obj, store), "collected"
            except TransportError as error:
                failures.append(str(error))
        # A checksum, disk-write or atomic-replace error must NOT try another source.
    raise TransportError("unavailable " + obj["key"] + ": " + ", ".join(failures))


def collect_objects(objects, store, mirror_base=None):
    result = {"objects": len(objects), "cached": 0, "collected": 0, "bytes": 0, "errors": [],
              "self_mirror": "configured" if mirror_base else "自留回落不可用（未配置 --mirror-base）"}
    for obj in objects:
        try:
            path, status = collect_one(obj, store, mirror_base)
            result[status] += 1
            result["bytes"] += path.stat().st_size
        except TransportError as error:
            result["errors"].append({"key": obj["key"], "error": str(error)})
        except (InputError, OSError) as error:
            result["errors"].append({"key": obj["key"], "error": str(error)})
            result["stopped"] = True
            break
    return result


def verify_objects(objects, store):
    result = {"objects": len(objects), "verified": 0, "bytes": 0, "errors": []}
    for obj in objects:
        path = store / obj["key"]
        try:
            verify_file(path, obj)
            result["verified"] += 1
            result["bytes"] += path.stat().st_size
        except (InputError, OSError) as error:
            result["errors"].append({"key": obj["key"], "error": str(error)})
    return result


def import_files(objects, paths, store):
    """Identify originals by bytes, not by a cache's possibly ambiguous filename."""
    wanted = {obj["key"]: obj for obj in objects}
    algorithms = {obj["algorithm"] for obj in objects}
    result = {"files": len(paths), "imported": 0, "cached": 0, "bytes": 0, "errors": []}
    for path in paths:
        try:
            digests = {name: hashlib.new(name) for name in algorithms}
            with path.open("rb") as source:
                while chunk := source.read(1024 * 1024):
                    for digest in digests.values():
                        digest.update(chunk)
            keys = [name + "-" + digest.hexdigest() for name, digest in digests.items()]
            matches = [wanted[key] for key in keys if key in wanted]
            if not matches:
                raise InputError("not a pinned original in the selected inventory")
            for obj in matches:
                destination = store / obj["key"]
                if destination.exists():
                    verify_file(destination, obj)
                    result["cached"] += 1
                else:
                    with path.open("rb") as source:
                        store_stream(source, obj, store)
                    result["imported"] += 1
                result["bytes"] += destination.stat().st_size
        except (InputError, OSError) as error:
            result["errors"].append({"file": str(path), "error": str(error)})
    return result


def warm_npm(lock, repo, store, mirror_base, npm, npm_cache, userconfig, globalconfig):
    """Populate npm's public cache API from verified raw archives; do not run npm ci."""
    before = lock.read_bytes()
    for config in (userconfig, globalconfig):
        if config.read_bytes() != b"":
            raise InputError("npm configuration file must be empty: " + str(config))
    checked_url(mirror_base, base=True)
    objects = npm_inventory([lock], repo)
    result = collect_objects(objects, store, mirror_base)
    result["npm_batches"] = 0
    if result["errors"]:
        return result
    environment = os.environ.copy()
    environment["PATH"] = str(npm.parent) + os.pathsep + environment.get("PATH", "")
    for start in range(0, len(objects), 64):
        # npm infers a directory from extensionless names. Same-filesystem .tgz
        # hard links give its public cache API file specs without copying originals.
        with tempfile.TemporaryDirectory(prefix=".npm-", dir=store) as temporary:
            paths = []
            for obj in objects[start:start + 64]:
                alias = Path(temporary) / (obj["key"] + ".tgz")
                os.link(store / obj["key"], alias)
                paths.append(str(alias))
            command = [str(npm), "cache", "add", *paths, "--offline", "--ignore-scripts",
                       "--no-audit", "--no-fund", "--cache", str(npm_cache),
                       "--prefix", temporary, "--userconfig", str(userconfig),
                       "--globalconfig", str(globalconfig)]
            completed = subprocess.run(command, cwd=temporary, env=environment, stdout=sys.stderr)
        if completed.returncode:
            result["errors"].append({"error": "npm cache add failed", "exit_code": completed.returncode})
            break
        result["npm_batches"] += 1
    if lock.read_bytes() != before:
        raise InputError("lock changed during npm warming; abort before npm ci")
    return result


# These lock declarations do not request a separate source bundle. This is a
# mechanical triage list, not a finding about an archive's actual contents.
SOURCE_OPTIONAL_LICENSES = {
    "0BSD", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "BlueOak-1.0.0",
    "CC0-1.0", "ISC", "MIT", "Python-2.0", "Unlicense",
}
RELEASE_MANIFEST = "runtime-input-release.json"


def source_optional(license_value):
    if not isinstance(license_value, str):
        return False
    # Only the simple AND/OR expressions used by the current locks are accepted.
    if license_value.startswith("(") and license_value.endswith(")"):
        license_value = license_value[1:-1]
    licenses = re.split(r" (?:AND|OR) ", license_value)
    return all(token in SOURCE_OPTIONAL_LICENSES for token in licenses)


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def inventory_digest(objects):
    return hashlib.sha256(json_bytes(objects)).hexdigest()


def read_release_json(path):
    def unique(pairs):
        result = {}
        for name, value in pairs:
            if name in result:
                raise InputError("duplicate JSON field: " + name)
            result[name] = value
        return result
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique)


def release_requirements(objects, require_source=()):
    """Group actual gaps, without asking the owner to re-enter the inventory."""
    groups = {}
    required = set()
    proot_sources = [obj["key"] for obj in objects if any(
        ref["kind"] == "proot_source_for_COPYING" for ref in obj["refs"])]
    for obj in objects:
        for ref in obj["refs"]:
            kind = ref["kind"]
            if kind == "npm":
                name = "npm:" + ref["name"] + "@" + str(ref["version"])
                reason = "npm license/source coverage; lock metadata is not a bundled-component inventory"
                if not source_optional(ref["license"]) or obj["key"] in require_source:
                    required.add(name)
            elif kind in ("ubuntu_deb", "termux_jni"):
                name = kind + ":" + ref["name"].split("_", 1)[0]
                reason = "binary license/source version and packaging materials are not in the lock"
                required.add(name)
            else:
                name = "node" if kind.startswith("node_guest") else kind
                reason = "archive component/license/source coverage is not in the pins"
                required.add(name)
            row = groups.setdefault(name, {"id": name, "inputs": [], "refs": [],
                                            "reason": reason, "source_candidates": []})
            if obj["key"] not in row["inputs"]:
                row["inputs"].append(obj["key"])
            if ref not in row["refs"]:
                row["refs"].append(ref)
            if name in ("termux_jni:proot", "proot_source_for_COPYING"):
                # Already pinned bytes; not a claim that the Termux recipe and
                # every patch/build input have also been retained.
                row["source_candidates"] = proot_sources
    return [groups[key] for key in sorted(required)]


def release_plan(objects, store=None, require_source=()):
    unknown = set(require_source) - {obj["key"] for obj in objects}
    if unknown:
        raise InputError("source review requested for unknown inputs: " + ", ".join(sorted(unknown)))
    requirements = release_requirements(objects, set(require_source))
    hints = {}
    if store is not None:
        needed = {key for row in requirements for key in row["inputs"]}
        for obj in objects:
            path = store / obj["key"]
            if obj["key"] not in needed or not path.exists() or not any(
                    ref["kind"] == "npm" for ref in obj["refs"]):
                continue
            verify_file(path, obj)
            # Read only metadata from verified cached npm bytes. Never extract
            # files, run package scripts, or resolve/download a guessed source.
            with tarfile.open(path, "r|*") as archive:
                for member in archive:
                    if member.name.removeprefix("./") != "package/package.json":
                        continue
                    if not member.isfile() or member.size > 1024 * 1024:
                        raise InputError("unsupported npm metadata: " + obj["key"])
                    data = json.loads(archive.extractfile(member).read().decode("utf-8"))
                    if not isinstance(data, dict):
                        raise InputError("npm metadata must be an object: " + obj["key"])
                    hints[obj["key"]] = {key: data[key] for key in
                                         ("name", "version", "license", "repository", "gitHead") if key in data}
                    break
    return {"schema": 1, "inventory_sha256": inventory_digest(objects),
            "requirements": [{**row, "coverage": None} for row in requirements],
            "artifacts": [], "source_hints": hints}


def release_objects(objects, plan):
    """Check declarations, not the semantic completeness of corresponding source."""
    if not isinstance(plan, dict) or set(plan) != {
            "schema", "inventory_sha256", "requirements", "artifacts", "source_hints"}:
        raise InputError("unsupported source plan fields")
    if type(plan["schema"]) is not int or plan["schema"] != 1 or plan["inventory_sha256"] != inventory_digest(objects):
        raise InputError("source plan does not match the current default inventory")
    if not isinstance(plan["requirements"], list) or not isinstance(plan["artifacts"], list) or not isinstance(plan["source_hints"], dict):
        raise InputError("malformed source plan lists")
    required = {row["id"] for row in release_requirements(objects)}
    expected = {row["id"]: row for row in release_requirements(objects, {obj["key"] for obj in objects})}
    rows = plan["requirements"]
    ids = [row.get("id") for row in rows if isinstance(row, dict)]
    if any(not isinstance(name, str) for name in ids) or len(ids) != len(set(ids)):
        raise InputError("source plan requirement identifiers must be unique strings")
    if not required.issubset(ids) or any(not isinstance(row, dict) or
            row.get("id") not in expected or set(row) != set(expected[row["id"]]) | {"coverage"} or
            {key: value for key, value in row.items() if key != "coverage"} != expected[row["id"]]
            for row in rows):
        raise InputError("source plan requirements changed; regenerate from the current inventory")
    all_objects = {obj["key"]: obj for obj in objects}
    for artifact in plan["artifacts"]:
        if not isinstance(artifact, dict) or set(artifact) != {"name", "sha256", "size", "urls", "origin"}:
            raise InputError("unsupported source artifact fields")
        if not isinstance(artifact["name"], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*", artifact["name"]):
            raise InputError("source artifact name must be a portable basename")
        if not isinstance(artifact["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", artifact["sha256"]):
            raise InputError("source artifact needs an independently verified SHA256")
        if type(artifact["size"]) is not int or artifact["size"] <= 0:
            raise InputError("source artifact needs an exact positive byte size")
        if not isinstance(artifact["origin"], str) or not artifact["origin"].strip():
            raise InputError("source artifact needs its exact upstream/build provenance")
        if not isinstance(artifact["urls"], list) or any(not isinstance(url, str) for url in artifact["urls"]):
            raise InputError("source artifact URLs must be a list")
        for url in artifact["urls"]:
            checked_url(url, base=True)
            if urllib.parse.urlsplit(url).scheme != "https":
                raise InputError("published source URLs must use HTTPS; use --import-file for local files")
        key = "sha256-" + artifact["sha256"]
        if key in all_objects:
            raise InputError("duplicate artifact; reference the existing object: " + key)
        all_objects[key] = {"key": key, "algorithm": "sha256", "digest": artifact["sha256"],
                            "size": artifact["size"], "urls": artifact["urls"], "refs": []}
    pending = [row["id"] for row in rows if row["coverage"] is None]
    if pending:
        raise InputError("unresolved source coverage: " + ", ".join(pending))
    used = set()
    for row in rows:
        coverage = row["coverage"]
        if not isinstance(coverage, dict) or set(coverage) != {"license", "sources", "build", "evidence"}:
            raise InputError("coverage needs license, sources, build and evidence: " + row["id"])
        if any(not isinstance(coverage[key], str) or not coverage[key].strip() for key in ("license", "evidence")):
            raise InputError("missing license/source evidence: " + row["id"])
        if coverage["license"].upper() in ("UNKNOWN", "NOASSERTION", "PENDING"):
            raise InputError("unresolved license: " + row["id"])
        needs_source = not source_optional(coverage["license"]) or any(
            ref["kind"] == "npm" and ref["license"] and not source_optional(ref["license"])
            for ref in row["refs"])
        for field in ("sources", "build"):
            keys = coverage[field]
            if not isinstance(keys, list) or any(not isinstance(key, str) or key not in all_objects for key in keys):
                raise InputError("unknown material reference in " + field + ": " + row["id"])
            if len(keys) != len(set(keys)) or (needs_source and not keys):
                raise InputError("missing/duplicate corresponding " + field + ": " + row["id"])
            used.update(keys)
    extra = set(all_objects) - {obj["key"] for obj in objects}
    if extra - used:
        raise InputError("unreferenced source artifacts: " + ", ".join(sorted(extra - used)))
    return [all_objects[key] for key in sorted(all_objects)]


def release_file(path, obj):
    if not stat.S_ISREG(path.lstat().st_mode):
        raise InputError("release member is not a regular file: " + path.name)
    digests = {name: hashlib.new(name) for name in {"sha256", obj["algorithm"]}}
    size = 0
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            size += len(chunk)
            for digest in digests.values():
                digest.update(chunk)
    if digests[obj["algorithm"]].hexdigest() != obj["digest"]:
        raise InputError("checksum mismatch: " + obj["key"])
    if obj["size"] is not None and size != obj["size"]:
        raise InputError("size mismatch: " + obj["key"])
    return {"name": obj["key"], "size": size, "sha256": digests["sha256"].hexdigest()}


def release_sums(files, manifest_bytes):
    hashes = {row["name"]: row["sha256"] for row in files}
    hashes[RELEASE_MANIFEST] = hashlib.sha256(manifest_bytes).hexdigest()
    return "".join(hashes[name] + "  " + name + "\n" for name in sorted(hashes))


def check_release(objects, plan, output):
    all_objects = release_objects(objects, plan)
    expected_names = {obj["key"] for obj in all_objects} | {RELEASE_MANIFEST, "SHA256SUMS"}
    if output.is_symlink() or not output.is_dir():
        raise InputError("release must be a real directory")
    if {path.name for path in output.iterdir()} != expected_names:
        raise InputError("release file set differs: missing, extra or incomplete files")
    for name in (RELEASE_MANIFEST, "SHA256SUMS"):
        if not stat.S_ISREG((output / name).lstat().st_mode):
            raise InputError("release metadata is not a regular file: " + name)
    files = [release_file(output / obj["key"], obj) for obj in all_objects]
    expected = {"schema": 1, "scope": "current-defaults", "inventory": objects,
                "source_plan": plan, "files": files}
    manifest_bytes = (output / RELEASE_MANIFEST).read_bytes()
    if read_release_json(output / RELEASE_MANIFEST) != expected:
        raise InputError("release manifest differs from the current inventory, source plan or bytes")
    if (output / "SHA256SUMS").read_bytes() != release_sums(files, manifest_bytes).encode("utf-8"):
        raise InputError("release SHA256SUMS differs")
    return {"status": "VERIFIED_DECLARED_MATERIALS", "objects": len(all_objects),
            "input_objects": len(objects), "material_objects": len(all_objects) - len(objects),
            "upload_files": len(expected_names), "bytes": sum(row["size"] for row in files),
            "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(), "errors": []}


def prepare_release(objects, plan, store, output, mirror_base=None, import_paths=()):
    all_objects = release_objects(objects, plan)  # Reject gaps before fetching anything.
    if output.exists() or output.is_symlink():
        raise InputError("release output must not already exist")
    if store.resolve().is_relative_to(output.resolve()) or output.resolve().is_relative_to(store.resolve()):
        raise InputError("release output and input store must be separate directories")
    if mirror_base:
        checked_url(mirror_base, base=True)
    if import_paths:
        imported = import_files(all_objects, import_paths, store)
        if imported["errors"]:
            return imported
    collected = collect_objects(all_objects, store, mirror_base)
    if collected["errors"]:
        return collected
    output.parent.mkdir(parents=True, exist_ok=True)
    output.mkdir()  # Exclusive creation: never replace another release directory.
    complete = False
    try:
        (output / "INCOMPLETE").write_text("preparing\n", encoding="utf-8")
        files = []
        for obj in all_objects:
            # Copy verified bytes, not hard links that a later cache write could
            # mutate. store_stream checks hashes/size again while copying.
            with (store / obj["key"]).open("rb") as source:
                store_stream(source, obj, output)
            files.append(release_file(output / obj["key"], obj))
        manifest = {"schema": 1, "scope": "current-defaults", "inventory": objects,
                    "source_plan": plan, "files": files}
        manifest_bytes = json_bytes(manifest)
        (output / RELEASE_MANIFEST).write_bytes(manifest_bytes)
        (output / "SHA256SUMS").write_text(release_sums(files, manifest_bytes), encoding="utf-8")
        (output / "INCOMPLETE").unlink()
        result = check_release(objects, plan, output)
        complete = True
        return result
    finally:
        if not complete:
            shutil.rmtree(output)


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    for name in ("inventory", "collect", "import", "verify", "warm-npm"):
        command = commands.add_parser(name)
        command.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
        if name != "warm-npm":
            selection = command.add_mutually_exclusive_group()
            selection.add_argument("--npm-lock", type=Path, action="append", help="replace default npm lock selection")
            selection.add_argument("--all-locks", action="store_true", help="include historical npm locks")
        if name != "inventory":
            command.add_argument("--store", type=Path, required=True)
        if name in ("collect", "warm-npm"):
            command.add_argument("--mirror-base", required=name == "warm-npm", help="explicit HTTPS or file:// flat object directory")
        if name == "import":
            command.add_argument("files", type=Path, nargs="+")
        if name == "warm-npm":
            for option in ("lock", "npm", "npm-cache", "userconfig", "globalconfig"):
                command.add_argument("--" + option, type=Path, required=True)
    for name in ("release-plan", "prepare-release", "check-release"):
        command = commands.add_parser(name)
        command.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
        if name in ("release-plan", "prepare-release"):
            command.add_argument("--store", type=Path, required=name == "prepare-release")
        if name == "release-plan":
            command.add_argument("--require-source", action="append", default=[],
                                 help="also review this input key, e.g. a bundled component absent from the lock license")
        if name != "release-plan":
            command.add_argument("--plan", type=Path, required=True)
            command.add_argument("--output", type=Path, required=True)
        if name == "prepare-release":
            command.add_argument("--mirror-base")
            command.add_argument("--import-file", type=Path, action="append", default=[])
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        repo = args.repo.absolute()
        if args.command == "warm-npm":
            result = warm_npm(args.lock.absolute(), repo, args.store.absolute(), args.mirror_base,
                              args.npm.absolute(), args.npm_cache.absolute(),
                              args.userconfig.absolute(), args.globalconfig.absolute())
        elif args.command in ("release-plan", "prepare-release", "check-release"):
            objects = inventory(repo)  # A release always covers all current defaults.
            if args.command == "release-plan":
                result = release_plan(objects, args.store.absolute() if args.store else None, args.require_source)
            else:
                plan = read_release_json(args.plan)
                if args.command == "prepare-release":
                    result = prepare_release(objects, plan, args.store.absolute(), args.output.absolute(),
                                             args.mirror_base, args.import_file)
                else:
                    result = check_release(objects, plan, args.output.absolute())
        else:
            locks = [path.absolute() for path in args.npm_lock] if args.npm_lock else None
            objects = inventory(repo, locks, args.all_locks)
            if args.command == "inventory":
                result = {"schema": 1, "object_count": len(objects), "objects": objects}
            elif args.command == "collect":
                if args.mirror_base:
                    checked_url(args.mirror_base, base=True)
                result = collect_objects(objects, args.store.absolute(), args.mirror_base)
            elif args.command == "verify":
                result = verify_objects(objects, args.store.absolute())
            else:
                result = import_files(objects, args.files, args.store.absolute())
    except (InputError, OSError, ValueError, KeyError, tarfile.TarError) as error:
        result = {"errors": [{"error": str(error)}]}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result.get("errors") else 0


if __name__ == "__main__":
    sys.exit(main())
