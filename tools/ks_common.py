"""Shared helpers for Karstscape dev tools (Python 3.12, standard library only)."""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / ".cache"
REFERENCE_DIR = ROOT / "reference"

# Official Mojang server jars we use. Values come from the version JSONs on
# piston-meta.mojang.com (checked 2026-11-16).
SERVER_JARS = {
    "26.3": {
        "url": "https://piston-data.mojang.com/v1/objects/33680f5f2ac32864d6d7cf5e56a705fdb3e05f4c/server.jar",
        "sha1": "33680f5f2ac32864d6d7cf5e56a705fdb3e05f4c",
        "size": 62294556,
        "java": 25,
    },
    "1.21.11": {
        "url": "https://piston-data.mojang.com/v1/objects/64bb6d763bed0a9f1d632ec347938594144943ed/server.jar",
        "sha1": "64bb6d763bed0a9f1d632ec347938594144943ed",
        "size": 56327581,
        "java": 21,
    },
}

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def setup_stdio() -> None:
    """Never crash on unencodable characters when printing to a GBK console."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass


# ---------------------------------------------------------------- Java ----

def _java_release_major(home: Path) -> int | None:
    release = home / "release"
    if not release.is_file():
        return None
    for line in release.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("JAVA_VERSION="):
            ver = line.split("=", 1)[1].strip().strip('"')
            head = ver.split(".")[0]
            try:
                major = int(head)
            except ValueError:
                return None
            return 8 if major == 1 else major
    return None


def _candidate_java_homes() -> list[Path]:
    cands: list[Path] = []
    for env in ("KARSTSCAPE_JAVA_HOME", "JAVA_HOME"):
        val = os.environ.get(env)
        if val:
            cands.append(Path(val))
    if os.name == "nt":
        roots = [Path(os.environ.get("ProgramFiles", r"C:\Program Files"))]
        for root in roots:
            for vendor in ("Java", "Eclipse Adoptium", "Microsoft", "Zulu", "Amazon Corretto"):
                base = root / vendor
                if base.is_dir():
                    cands.extend(sorted(p for p in base.iterdir() if p.is_dir() and "jdk" in p.name.lower()))
    else:
        for base in (Path("/usr/lib/jvm"), Path("/Library/Java/JavaVirtualMachines")):
            if base.is_dir():
                for p in base.iterdir():
                    cands.append(p / "Contents" / "Home" if (p / "Contents" / "Home").is_dir() else p)
    return cands


def find_java_home(major: int = 25) -> Path:
    """Return a JDK home whose release file says exactly `major` (prefers env overrides)."""
    exe = "java.exe" if os.name == "nt" else "java"
    for home in _candidate_java_homes():
        if (home / "bin" / exe).is_file() and _java_release_major(home) == major:
            return home
    raise SystemExit(
        f"ERROR: no JDK {major} found. Set KARSTSCAPE_JAVA_HOME to a JDK {major} install."
    )


def java_exe(home: Path) -> Path:
    return home / "bin" / ("java.exe" if os.name == "nt" else "java")


def env_with_java(home: Path) -> dict[str, str]:
    """Copy of os.environ with JAVA_HOME pointing at `home` (for one subprocess only)."""
    env = dict(os.environ)
    env["JAVA_HOME"] = str(home)
    env["PATH"] = str(home / "bin") + os.pathsep + env.get("PATH", "")
    return env


# ---------------------------------------------------------------- files ---

def sha1_file(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def tree_hash(root: Path, exclude: tuple[str, ...] = ()) -> tuple[str, int]:
    """sha256 over (relative posix path, file sha256) for every file; returns (hash, count)."""
    h = hashlib.sha256()
    count = 0
    files = sorted(p for p in root.rglob("*") if p.is_file())
    for p in files:
        rel = p.relative_to(root).as_posix()
        if rel in exclude:
            continue
        h.update(rel.encode("utf-8") + b"\0" + sha256_file(p).encode("ascii") + b"\n")
        count += 1
    return h.hexdigest(), count


def safe_rmtree(path: Path, allowed_parents: list[Path]) -> None:
    """Delete `path` only if it lies strictly inside one of `allowed_parents`."""
    path = path.resolve()
    for parent in allowed_parents:
        parent = parent.resolve()
        if path != parent and parent in path.parents:
            if path.exists():
                shutil.rmtree(path)
            return
    raise RuntimeError(f"refusing to delete {path}: not inside {allowed_parents}")


def loom_cache_server_jar(version: str) -> Path:
    gradle_home = Path(os.environ.get("GRADLE_USER_HOME", Path.home() / ".gradle"))
    return gradle_home / "caches" / "fabric-loom" / version / "minecraft-server.jar"


# ------------------------------------------------------------ processes ---

def kill_tree(pid: int, force: bool = True) -> None:
    """Terminate a process and all its children."""
    if os.name == "nt":
        args = ["taskkill", "/PID", str(pid), "/T"]
        if force:
            args.append("/F")
        subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        import signal
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL if force else signal.SIGTERM)
        except ProcessLookupError:
            pass


def pid_alive(pid: int) -> bool:
    if os.name == "nt":
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
            capture_output=True, text=True, errors="replace",
        ).stdout
        return f'"{pid}"' in out
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def process_command_line(pid: int) -> str:
    """Best-effort command line of a process ('' if unknown)."""
    if os.name == "nt":
        ps = (
            f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}' -ErrorAction SilentlyContinue)"
            ".CommandLine"
        )
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, text=True, errors="replace")
        return r.stdout.strip()
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
    except OSError:
        return ""


def decode_line(raw: bytes) -> str:
    """Decode a line of child-process output (UTF-8, falling back to GBK) and strip ANSI codes."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("gbk", errors="replace")
    return ANSI_RE.sub("", text).rstrip("\r\n")


def gradlew() -> list[str]:
    if os.name == "nt":
        return ["cmd", "/c", str(ROOT / "gradlew.bat")]
    return [str(ROOT / "gradlew")]
