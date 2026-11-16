"""Export vanilla Minecraft 26.3 data with the official data generator.

Output: reference/vanilla-26.3/  (gitignored; Mojang data is not redistributed)
  data/...            output of `--server` (the full vanilla data pack, incl. worldgen)
  reports/...         output of `--reports` (registries.json, biome_parameters, ...)
  version.json        copied from the inner server jar
  MANIFEST.json       how it was produced + counts + tree hash + datagen-vs-jar comparison

Usage (from the project root):
  python tools/export_vanilla_data.py              # export if missing or stale
  python tools/export_vanilla_data.py --force      # always re-export
  python tools/export_vanilla_data.py --baseline 1.21.11
      # additionally extract the worldgen JSON shipped inside the 1.21.11 jar to
      # reference/vanilla-1.21.11-shipped/ (no datagen run; old-format comparison only)
"""
from __future__ import annotations

import argparse
import io
import json
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ks_common as ks  # noqa: E402

TOOL_SCHEMA = 1
VERSION = "26.3"
DATAGEN_ARGS = ["--server", "--reports"]
DATAGEN_TIMEOUT_S = 900


def log(msg: str) -> None:
    print(f"[export] {msg}", flush=True)


# ------------------------------------------------------------------ jar ----

def ensure_server_jar(version: str) -> Path:
    """Return a sha1-verified copy of the official server jar under .cache/mojang/<version>/."""
    info = ks.SERVER_JARS[version]
    dest = ks.CACHE_DIR / "mojang" / version / "server.jar"
    if dest.is_file() and ks.sha1_file(dest) == info["sha1"]:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)

    loom_jar = ks.loom_cache_server_jar(version)
    if loom_jar.is_file() and loom_jar.stat().st_size == info["size"] and ks.sha1_file(loom_jar) == info["sha1"]:
        log(f"copying verified server jar from Loom cache: {loom_jar}")
        shutil.copyfile(loom_jar, dest)
        return dest

    log(f"downloading {info['url']} ({info['size']:,} bytes)")
    part = dest.with_suffix(".part")
    req = urllib.request.Request(info["url"], headers={"User-Agent": "karstscape-dev-tools"})
    with urllib.request.urlopen(req, timeout=120) as resp, open(part, "wb") as f:
        shutil.copyfileobj(resp, f, 1 << 20)
    size = part.stat().st_size
    sha1 = ks.sha1_file(part)
    if size != info["size"] or sha1 != info["sha1"]:
        part.unlink(missing_ok=True)
        raise SystemExit(f"ERROR: downloaded jar mismatch (size {size}, sha1 {sha1}); refusing to use it")
    part.replace(dest)
    return dest


def inner_server_jar(bundler: zipfile.ZipFile, version: str) -> zipfile.ZipFile:
    name = f"META-INF/versions/{version}/server-{version}.jar"
    return zipfile.ZipFile(io.BytesIO(bundler.read(name)))


# ------------------------------------------------------------- datagen ----

def run_java(java: Path, jar: Path, args: list[str], cwd: Path, log_path: Path, timeout: int) -> int:
    cmd = [str(java), "-Xmx2G", "-DbundlerMainClass=net.minecraft.data.Main", "-jar", str(jar), *args]
    log("running: " + " ".join(cmd))
    with open(log_path, "ab") as lf:
        lf.write(("\n$ " + " ".join(cmd) + "\n").encode("utf-8"))
        lf.flush()
        try:
            proc = subprocess.run(cmd, cwd=cwd, stdout=lf, stderr=subprocess.STDOUT, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise SystemExit(f"ERROR: data generator timed out after {timeout}s (see {log_path})")
    return proc.returncode


def capture_help(java: Path, jar: Path, cwd: Path) -> str:
    cmd = [str(java), "-DbundlerMainClass=net.minecraft.data.Main", "-jar", str(jar), "--help"]
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, timeout=300)
    text = (r.stdout + r.stderr).decode("utf-8", errors="replace")
    # keep only the option table (drop bundler / logger noise)
    lines = [ln.rstrip() for ln in text.splitlines()]
    start = next((i for i, ln in enumerate(lines) if ln.lstrip().startswith("Option")), 0)
    return "\n".join(lines[start:]).strip()


def java_version_string(java: Path) -> str:
    r = subprocess.run([str(java), "-version"], capture_output=True)
    return (r.stderr or r.stdout).decode("utf-8", errors="replace").strip()


# ------------------------------------------------------------- compare ----

def compare_with_jar(out_dir: Path, inner: zipfile.ZipFile) -> dict:
    """Compare datagen data/**/*.json with the JSON shipped in the inner server jar."""
    jar_json = {n for n in inner.namelist() if n.startswith("data/") and n.endswith(".json")}
    gen_json = {p.relative_to(out_dir).as_posix() for p in (out_dir / "data").rglob("*.json")}
    identical, different = 0, []
    for name in sorted(jar_json & gen_json):
        a = json.loads(inner.read(name).decode("utf-8"))
        b = json.loads((out_dir / name).read_text(encoding="utf-8"))
        if a == b:
            identical += 1
        else:
            different.append(name)
    only_gen = sorted(gen_json - jar_json)
    only_jar = sorted(jar_json - gen_json)
    wg_prefix = "data/minecraft/worldgen/"
    return {
        "json_in_jar": len(jar_json),
        "json_in_datagen": len(gen_json),
        "identical": identical,
        "different": len(different),
        "different_examples": different[:20],
        "only_in_datagen": len(only_gen),
        "only_in_datagen_examples": only_gen[:20],
        "only_in_jar": len(only_jar),
        "only_in_jar_examples": only_jar[:20],
        "worldgen_identical": sum(1 for n in jar_json & gen_json if n.startswith(wg_prefix) and n not in different),
        "worldgen_in_jar": sum(1 for n in jar_json if n.startswith(wg_prefix)),
    }


def worldgen_counts(data_dir: Path) -> dict[str, int]:
    wg = data_dir / "minecraft" / "worldgen"
    counts: Counter[str] = Counter()
    if wg.is_dir():
        for p in wg.rglob("*.json"):
            counts[p.relative_to(wg).parts[0]] += 1
    return dict(sorted(counts.items()))


# ---------------------------------------------------------------- main ----

def manifest_is_current(out_dir: Path, jar_sha1: str) -> bool:
    mf = out_dir / "MANIFEST.json"
    if not mf.is_file():
        return False
    try:
        m = json.loads(mf.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    if m.get("tool_schema") != TOOL_SCHEMA or m.get("jar_sha1") != jar_sha1 or m.get("datagen_args") != DATAGEN_ARGS:
        return False
    th, _ = ks.tree_hash(out_dir, exclude=("MANIFEST.json",))
    return th == m.get("tree_sha256")


def export_26_3(force: bool) -> Path:
    out_dir = ks.REFERENCE_DIR / f"vanilla-{VERSION}"
    jar = ensure_server_jar(VERSION)
    jar_sha1 = ks.SERVER_JARS[VERSION]["sha1"]
    if not force and manifest_is_current(out_dir, jar_sha1):
        log(f"up to date: {out_dir} (use --force to re-export)")
        return out_dir

    java_home = ks.find_java_home(ks.SERVER_JARS[VERSION]["java"])
    java = ks.java_exe(java_home)
    work = ks.CACHE_DIR / f"datagen-{VERSION}"
    ks.safe_rmtree(work, [ks.CACHE_DIR])
    work.mkdir(parents=True)
    gen = work / "generated"
    datagen_log = work / "datagen.log"

    help_text = capture_help(java, jar, work)
    t0 = time.monotonic()
    rc = run_java(java, jar, [*DATAGEN_ARGS, "--output", str(gen)], work, datagen_log, DATAGEN_TIMEOUT_S)
    elapsed = time.monotonic() - t0
    if rc != 0:
        raise SystemExit(f"ERROR: data generator exited with {rc} (see {datagen_log})")
    if not (gen / "data" / "minecraft" / "worldgen").is_dir():
        raise SystemExit(f"ERROR: no data/minecraft/worldgen in datagen output (see {datagen_log})")
    log(f"data generator finished in {elapsed:.1f}s")

    tmp = ks.REFERENCE_DIR / f"vanilla-{VERSION}.tmp"
    ks.safe_rmtree(tmp, [ks.REFERENCE_DIR])
    tmp.mkdir(parents=True)
    for child in gen.iterdir():
        if child.name == ".cache":
            continue
        if child.is_dir():
            shutil.copytree(child, tmp / child.name)
        else:
            shutil.copy2(child, tmp / child.name)

    with zipfile.ZipFile(jar) as bundler, inner_server_jar(bundler, VERSION) as inner:
        (tmp / "version.json").write_bytes(inner.read("version.json"))
        comparison = compare_with_jar(tmp, inner)

    th, nfiles = ks.tree_hash(tmp, exclude=("MANIFEST.json",))
    manifest = {
        "tool_schema": TOOL_SCHEMA,
        "minecraft_version": VERSION,
        "jar_url": ks.SERVER_JARS[VERSION]["url"],
        "jar_sha1": jar_sha1,
        "datagen_args": DATAGEN_ARGS,
        "command": f'java -DbundlerMainClass=net.minecraft.data.Main -jar server.jar {" ".join(DATAGEN_ARGS)} --output <dir>',
        "java_version": java_version_string(java),
        "datagen_help": help_text,
        "datagen_seconds": round(elapsed, 1),
        "file_count": nfiles,
        "worldgen_counts": worldgen_counts(tmp / "data"),
        "worldgen_total": sum(worldgen_counts(tmp / "data").values()),
        "comparison_with_jar": comparison,
        "tree_sha256": th,
    }
    (tmp / "MANIFEST.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    ks.safe_rmtree(out_dir, [ks.REFERENCE_DIR])
    tmp.rename(out_dir)
    log(f"wrote {out_dir} ({nfiles} files, worldgen {manifest['worldgen_total']})")
    log(f"worldgen per registry: {manifest['worldgen_counts']}")
    c = comparison
    log(f"datagen vs jar: identical {c['identical']}, different {c['different']}, "
        f"only-datagen {c['only_in_datagen']}, only-jar {c['only_in_jar']}; "
        f"worldgen identical {c['worldgen_identical']}/{c['worldgen_in_jar']}")
    return out_dir


def extract_baseline(version: str) -> Path:
    """Extract worldgen JSON shipped inside an older server jar (no datagen run)."""
    out_dir = ks.REFERENCE_DIR / f"vanilla-{version}-shipped"
    jar = ensure_server_jar(version)
    sha1 = ks.SERVER_JARS[version]["sha1"]
    mf = out_dir / "MANIFEST.json"
    if mf.is_file() and json.loads(mf.read_text(encoding="utf-8")).get("jar_sha1") == sha1:
        log(f"up to date: {out_dir}")
        return out_dir
    tmp = ks.REFERENCE_DIR / f"vanilla-{version}-shipped.tmp"
    ks.safe_rmtree(tmp, [ks.REFERENCE_DIR])
    tmp.mkdir(parents=True)
    prefixes = ("data/minecraft/worldgen/", "data/minecraft/tags/worldgen/",
                "data/minecraft/dimension/", "data/minecraft/dimension_type/")
    n = 0
    with zipfile.ZipFile(jar) as bundler, inner_server_jar(bundler, version) as inner:
        for name in inner.namelist():
            if name.endswith(".json") and name.startswith(prefixes):
                target = tmp / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(inner.read(name))
                n += 1
        (tmp / "version.json").write_bytes(inner.read("version.json"))
    manifest = {
        "kind": "shipped (extracted from server jar, NOT datagen output)",
        "minecraft_version": version,
        "jar_url": ks.SERVER_JARS[version]["url"],
        "jar_sha1": sha1,
        "file_count": n,
        "worldgen_counts": worldgen_counts(tmp / "data"),
    }
    (tmp / "MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    ks.safe_rmtree(out_dir, [ks.REFERENCE_DIR])
    tmp.rename(out_dir)
    log(f"wrote {out_dir} ({n} files)")
    return out_dir


def main() -> int:
    ks.setup_stdio()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true", help="re-export even if up to date")
    ap.add_argument("--baseline", choices=[v for v in ks.SERVER_JARS if v != VERSION],
                    help="also extract an older version's shipped worldgen JSON for comparison")
    args = ap.parse_args()
    ks.REFERENCE_DIR.mkdir(exist_ok=True)
    export_26_3(args.force)
    if args.baseline:
        extract_baseline(args.baseline)
    return 0


if __name__ == "__main__":
    sys.exit(main())
