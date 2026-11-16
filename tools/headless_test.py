"""Headless self-test: start the dev dedicated server, create a world, run commands, stop, check logs.

  python tools/headless_test.py --profile vanilla --fresh
  python tools/headless_test.py --profile vanilla --fresh --update-baseline   # record WARN baseline
  python tools/headless_test.py --profile vanilla --fresh --repeat 2          # determinism hook

Profiles: tools/headless/profiles.toml. Results: build/headless/<UTC time>-<profile>/
(console.log, latest.log, report.json, summary.md, collected files).

Exit codes: 0 pass | 1 build/infrastructure | 2 timeout | 3 ERROR or crash |
            4 new or spammy WARN | 5 a command's expected output never appeared |
            6 collected files differ between --repeat runs
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import tomllib
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ks_common as ks  # noqa: E402

RUN_DIR = ks.ROOT / "run-headless"
HEADLESS_DIR = ks.ROOT / "tools" / "headless"
RESULTS_DIR = ks.ROOT / "build" / "headless"
PORT = 25599

DONE_RE = re.compile(r"Done \([\d.,]+s\)! For help")
# Game-side fatal lines (26.3: MinecraftServer / server.Main / client). A broken datapack or worldgen JSON
# only logs "Failed to load datapacks, can't proceed..." and the JVM then exits with code 0.
CRASH_CONSOLE_RE = re.compile(
    r"---- Minecraft Crash Report ----|Crash report saved to|This crash report has been saved to|"
    r"Encountered an unexpected exception|Failed to start the minecraft server|"
    r"Failed to load datapacks, can't proceed")
INFRA_CONSOLE_RE = re.compile(r"BUILD FAILED|FAILED TO BIND TO PORT")
FATAL_CONSOLE_RE = re.compile(f"{CRASH_CONSOLE_RE.pattern}|{INFRA_CONSOLE_RE.pattern}")
STOPPED_RE = re.compile(r"Stopping server")
LOG_LINE_RE = re.compile(
    r"^\[(?P<time>\d\d:\d\d:\d\d)\] \[(?P<thread>[^\]]+)/(?P<level>[A-Z]+)\]:?\s*(?:\((?P<logger>[^)]*)\)\s*)?(?P<msg>.*)$")

EXIT_OK, EXIT_INFRA, EXIT_TIMEOUT, EXIT_ERROR, EXIT_WARN, EXIT_EXPECT, EXIT_NONDET = 0, 1, 2, 3, 4, 5, 6
SEVERITY = [EXIT_INFRA, EXIT_TIMEOUT, EXIT_ERROR, EXIT_EXPECT, EXIT_NONDET, EXIT_WARN]

SPAM_SAME = 20
SPAM_TOTAL = 200
STOP_TIMEOUT = 180
SETTLE_AFTER_DONE = 1.0


def log(msg: str) -> None:
    print(f"[headless] {msg}", flush=True)


def game_started(lines: list[str]) -> bool:
    """True once the game JVM has logged anything (Gradle's own output never matches LOG_LINE_RE)."""
    return any(LOG_LINE_RE.match(ln) for ln in lines)


def classify_failure(status: str, lines: list[str], idx: int | None) -> int:
    """Exit code for a wait that did not succeed: game-side problems are EXIT_ERROR, build/port EXIT_INFRA."""
    if status == "timeout":
        return EXIT_TIMEOUT
    if status == "fatal" and idx is not None:
        line = lines[idx]
        if CRASH_CONSOLE_RE.search(line):
            return EXIT_ERROR
        if "FAILED TO BIND TO PORT" in line:
            return EXIT_INFRA
        return EXIT_ERROR if game_started(lines[:idx]) else EXIT_INFRA  # BUILD FAILED
    return EXIT_ERROR if game_started(lines) else EXIT_INFRA  # exited without "Done ("


# ------------------------------------------------------------ properties ---

def escape_prop_value(v: str) -> str:
    """java.util.Properties escaping as Minecraft writes it (':' and '=' escaped, backslash doubled)."""
    out = v.replace("\\", "\\\\")
    for ch in ("=", ":", "#", "!"):
        out = out.replace(ch, "\\" + ch)
    return out


def known_property_keys() -> set[str]:
    p = HEADLESS_DIR / "server_properties_keys_26.3.txt"
    return {ln.strip() for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()} if p.is_file() else set()


def server_properties(profile: dict) -> dict[str, str]:
    return {
        "level-seed": str(profile["seed"]),
        "level-type": str(profile["level_type"]),
        "level-name": str(profile["level_name"]),
        "server-ip": "127.0.0.1",
        "server-port": str(PORT),
        "online-mode": "false",
        "enforce-secure-profile": "false",
        "max-tick-time": "-1",
        "pause-when-empty-seconds": "0",
        "enable-rcon": "false",
        "management-server-enabled": "false",
    }


def write_server_properties(props: dict[str, str]) -> None:
    known = known_property_keys()
    unknown = sorted(k for k in props if known and k not in known)
    if unknown:
        raise SystemExit(f"ERROR: unknown server.properties keys for 26.3: {unknown}")
    path = RUN_DIR / "server.properties"
    lines: list[str] = []
    seen: set[str] = set()
    if path.is_file():
        for ln in path.read_text(encoding="utf-8", errors="replace").splitlines():
            key = ln.split("=", 1)[0].strip() if "=" in ln and not ln.lstrip().startswith("#") else None
            if key in props:
                lines.append(f"{key}={escape_prop_value(props[key])}")
                seen.add(key)
            else:
                lines.append(ln)
    for k, v in props.items():
        if k not in seen:
            lines.append(f"{k}={escape_prop_value(v)}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


# ------------------------------------------------------------ processes ---

def sweep_stale_server() -> None:
    pid_file = RUN_DIR / "server.pid"
    if not pid_file.is_file():
        return
    try:
        pid = int(pid_file.read_text().strip())
    except ValueError:
        pid_file.unlink(missing_ok=True)
        return
    if ks.pid_alive(pid):
        cmd = ks.process_command_line(pid).lower()
        ours = "fabric.dli.env=server" in cmd and str(ks.ROOT).lower() in cmd
        started = ks.process_start_epoch(pid)
        # The server writes server.pid after it starts; a process that started later reused the PID.
        same_process = started is not None and started <= pid_file.stat().st_mtime + 5
        if ours and same_process:
            log(f"killing stale dev server pid {pid}")
            ks.kill_tree(pid, force=True)
            time.sleep(2)
        else:
            log(f"pid {pid} from server.pid is not our leftover server; leaving it alone")
    pid_file.unlink(missing_ok=True)


class RunLock:
    """One headless_test at a time: run-headless/ (server.properties, worlds, server.pid) is shared."""

    def __init__(self, path: Path):
        self.path = path
        self.fh = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = open(self.path, "a+")
        try:
            if os.name == "nt":
                import msvcrt
                self.fh.seek(0)
                msvcrt.locking(self.fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.fh.close()
            raise SystemExit("ERROR: another headless_test run is in progress (run-headless/.headless.lock)")
        return self

    def __exit__(self, *exc):
        try:
            if os.name == "nt":
                import msvcrt
                self.fh.seek(0)
                msvcrt.locking(self.fh.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        self.fh.close()
        return False


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


class ServerProcess:
    """gradlew <task> (default runHeadlessServer) with a reader thread that keeps every console line."""

    def __init__(self, java_home: Path, console_log: Path, extra_gradle_args: list[str],
                 task: str = "runHeadlessServer"):
        args = [*ks.gradlew(), "--no-daemon", "--console=plain", *extra_gradle_args, task]
        if os.name == "nt":
            platform_kw = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW}
        else:
            platform_kw = {"start_new_session": True}  # own process group, so kill_tree never hits us
        self.lines: list[str] = []
        self.cond = threading.Condition()
        self.console = open(console_log, "w", encoding="utf-8", newline="\n")
        self.proc = subprocess.Popen(args, cwd=ks.ROOT, env=ks.env_with_java(java_home),
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     **platform_kw)
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self) -> None:
        assert self.proc.stdout is not None
        for raw in iter(self.proc.stdout.readline, b""):
            line = ks.decode_line(raw)
            self.console.write(line + "\n")
            self.console.flush()
            with self.cond:
                self.lines.append(line)
                self.cond.notify_all()
        with self.cond:
            self.cond.notify_all()

    def wait_for(self, pattern: re.Pattern, start: int, timeout: float,
                 fatal: re.Pattern | None = None) -> tuple[str, int | None]:
        """Wait for a line matching `pattern` at index >= start. Returns (status, index)."""
        deadline = time.monotonic() + timeout
        idx = start
        with self.cond:
            while True:
                while idx < len(self.lines):
                    line = self.lines[idx]
                    if pattern.search(line):
                        return "ok", idx
                    if fatal is not None and fatal.search(line):
                        return "fatal", idx
                    idx += 1
                if self.proc.poll() is not None and not self.reader.is_alive():
                    return "exited", None
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return "timeout", None
                self.cond.wait(min(remaining, 1.0))

    def send(self, command: str) -> int | None:
        """Write a console command; returns the line index to search from, or None if the pipe is gone."""
        with self.cond:
            mark = len(self.lines)
        assert self.proc.stdin is not None
        try:
            self.proc.stdin.write((command + "\n").encode("utf-8"))
            self.proc.stdin.flush()
        except OSError:
            log(f"> {command}  (not sent: server input closed)")
            return None
        log(f"> {command}")
        return mark

    def wait_exit(self, timeout: float) -> bool:
        try:
            self.proc.wait(timeout=timeout)
            self.reader.join(timeout=10)
            return True
        except subprocess.TimeoutExpired:
            return False

    def kill(self) -> None:
        ks.kill_tree(self.proc.pid, force=True)
        self.wait_exit(30)

    def close(self) -> None:
        try:
            if self.proc.stdin and not self.proc.stdin.closed:
                self.proc.stdin.close()
        except OSError:
            pass
        self.console.close()


# ---------------------------------------------------------- log analysis ---

def parse_log(text: str) -> list[dict]:
    """Split a Minecraft/Fabric log into entries; continuation lines attach to the previous entry."""
    entries: list[dict] = []
    for line in text.splitlines():
        m = LOG_LINE_RE.match(line)
        if m:
            entries.append({"time": m["time"], "thread": m["thread"], "level": m["level"],
                            "logger": m["logger"] or "", "msg": m["msg"], "extra": []})
        elif entries:
            entries[-1]["extra"].append(line)
    return entries


_UUID_RE = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
_HEX0X_RE = re.compile(r"\b0x[0-9a-fA-F]+\b")
_IDHASH_RE = re.compile(r"(?<=@)[0-9a-fA-F]{1,8}\b")  # Object.toString() identity hashes, any length
_HEX_RE = re.compile(r"\b(?=[0-9a-fA-F]*\d)[0-9a-fA-F]{8,}\b")
_NUM_RE = re.compile(r"(?<![A-Za-z_])-?\d+(?:[.,]\d+)*")  # keep digits inside identifiers (layer_1, v2)


def normalize(text: str) -> str:
    """Make a log message comparable across runs (paths, ids, numbers)."""
    out = text.replace(str(ks.ROOT), "<ROOT>").replace(str(ks.ROOT).replace("\\", "/"), "<ROOT>")
    home = str(Path.home())
    out = out.replace(home, "<HOME>").replace(home.replace("\\", "/"), "<HOME>")
    out = _UUID_RE.sub("<UUID>", out)
    out = _HEX0X_RE.sub("<HEX>", out)
    out = _IDHASH_RE.sub("<HEX>", out)
    out = _HEX_RE.sub("<HEX>", out)
    out = _NUM_RE.sub("#", out)
    return out.strip()


def entry_key(e: dict) -> str:
    return f"{e['level']} ({e['logger']}) {normalize(e['msg'])}"


def read_patterns(path: Path) -> list[re.Pattern]:
    if not path.is_file():
        return []
    pats = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("#"):
            pats.append(re.compile(ln))
    return pats


def read_baseline(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    return {ln.rstrip("\n") for ln in path.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.startswith("#")}


def jvm_warnings(console_lines: list[str]) -> list[str]:
    """JVM-level 'WARNING:' lines (stderr of the game JVM, not part of latest.log)."""
    return [normalize(ln) for ln in console_lines if ln.startswith("WARNING:")]


def analyze(latest_log: str, console_lines: list[str], baseline: set[str],
            warn_ignore: list[re.Pattern], error_allow: list[re.Pattern]) -> dict:
    entries = parse_log(latest_log)
    errors, allowed_errors = [], []
    warn_counts: Counter[str] = Counter()
    for e in entries:
        if e["level"] in ("ERROR", "FATAL"):
            key = entry_key(e)
            (allowed_errors if any(p.search(key) for p in error_allow) else errors).append(
                {"key": key, "line": f"[{e['time']}] [{e['thread']}/{e['level']}] ({e['logger']}) {e['msg']}",
                 "extra": e["extra"][:15]})
        elif e["level"] == "WARN":
            key = entry_key(e)
            if not any(p.search(key) for p in warn_ignore):
                warn_counts[key] += 1
    for w in jvm_warnings(console_lines):
        key = f"jvm: {w}"
        if not any(p.search(key) for p in warn_ignore):
            warn_counts[key] += 1
    new_warns = sorted(k for k in warn_counts if k not in baseline)
    spam = sorted(k for k, n in warn_counts.items() if n > SPAM_SAME and not k.startswith("jvm: "))
    total_warn = sum(n for k, n in warn_counts.items() if not k.startswith("jvm: "))
    return {
        "log_entries": len(entries),
        "errors": errors,
        "allowed_errors": allowed_errors,
        "warn_counts": dict(sorted(warn_counts.items())),
        "warn_total": total_warn,
        "new_warns": new_warns,
        "spam_warns": spam,
        "spam_total": total_warn > SPAM_TOTAL,
    }


# ------------------------------------------------------------------ run ----

def crash_reports() -> set[str]:
    d = RUN_DIR / "crash-reports"
    return {p.name for p in d.iterdir()} if d.is_dir() else set()


def run_once(profile_name: str, profile: dict, args, java_home: Path, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    result: dict = {"profile": profile_name, "codes": [], "commands": [], "timings": {}}

    sweep_stale_server()
    if not port_free(PORT):
        result["codes"].append(EXIT_INFRA)
        result["infra"] = f"port {PORT} on 127.0.0.1 is in use"
        log("ERROR: " + result["infra"])
        return result
    RUN_DIR.mkdir(exist_ok=True)
    (RUN_DIR / "eula.txt").write_text("eula=true\n", encoding="utf-8")
    write_server_properties(server_properties(profile))
    world = RUN_DIR / profile["level_name"]
    if args.fresh and world.exists():
        ks.safe_rmtree(world, [RUN_DIR])
        log(f"deleted world {world.name}")
    crashes_before = crash_reports()
    logs_dir = RUN_DIR / "logs"
    gz_before = {p.name for p in logs_dir.glob("*.log.gz")} if logs_dir.is_dir() else set()
    old_latest = logs_dir / "latest.log"
    old_latest_mtime = old_latest.stat().st_mtime if old_latest.is_file() else None
    # log4j rotates the previous latest.log into a .log.gz at startup (OnStartupTriggeringPolicy).
    old_latest_text = old_latest.read_text(encoding="utf-8", errors="replace") if old_latest.is_file() else None

    t0 = time.monotonic()
    srv = ServerProcess(java_home, out_dir / "console.log", list(args.gradle_arg))
    server_died = False
    try:
        status, idx = srv.wait_for(DONE_RE, 0, args.timeout_start, FATAL_CONSOLE_RE)
        result["timings"]["start_s"] = round(time.monotonic() - t0, 1)
        if status != "ok":
            server_died = True
            result["codes"].append(classify_failure(status, srv.lines, idx))
            hit = srv.lines[idx] if idx is not None else ""
            result["infra"] = f"server did not reach 'Done (' ({status}{': ' + hit if hit else ''})"
            log("ERROR: " + result["infra"])
        else:
            log(f"server ready in {result['timings']['start_s']}s")
            time.sleep(SETTLE_AFTER_DONE)

        commands = [] if server_died else profile.get("commands", [])
        for n, cmd in enumerate(commands):
            mark = srv.send(cmd["send"])
            t1 = time.monotonic()
            if mark is None:
                st, idx = "exited", None
            else:
                st, idx = srv.wait_for(re.compile(cmd["expect"]), mark, float(cmd.get("timeout", 60)),
                                       FATAL_CONSOLE_RE)
            rec = {"send": cmd["send"], "expect": cmd["expect"], "status": st,
                   "seconds": round(time.monotonic() - t1, 1),
                   "matched": srv.lines[idx] if st in ("ok", "fatal") and idx is not None else None}
            result["commands"].append(rec)
            if st == "ok":
                log(f"  ok ({rec['seconds']}s): {rec['matched']}")
                continue
            log(f"ERROR: command {cmd['send']!r}: expected /{cmd['expect']}/ -> {st}")
            if st == "timeout":
                result["codes"].append(EXIT_EXPECT)
            else:
                server_died = True
                result["codes"].append(classify_failure(st, srv.lines, idx))
            # Stop here: a late answer to this command must not be credited to the next one.
            for rest in commands[n + 1:]:
                result["commands"].append({"send": rest["send"], "expect": rest["expect"], "status": "skipped",
                                           "seconds": 0.0, "matched": None})
            break

        if server_died or srv.proc.poll() is not None:
            if not srv.wait_exit(30):
                srv.kill()
        else:
            mark = srv.send("stop")
            t2 = time.monotonic()
            st = "exited" if mark is None else srv.wait_for(STOPPED_RE, mark, 60)[0]
            exited = srv.wait_exit(STOP_TIMEOUT)
            result["timings"]["stop_s"] = round(time.monotonic() - t2, 1)
            if st != "ok" or not exited:
                log("ERROR: server did not stop cleanly; killing process tree")
                result["codes"].append(EXIT_TIMEOUT)
                srv.kill()
        rc = srv.proc.returncode
        result["gradle_exit"] = rc
        if rc not in (0, None) and not ({EXIT_TIMEOUT, EXIT_ERROR, EXIT_INFRA} & set(result["codes"])):
            result["codes"].append(classify_failure("exited", srv.lines, None))
    except KeyboardInterrupt:
        srv.kill()
        raise
    finally:
        srv.close()
        sweep_stale_server()

    # latest.log of THIS run, plus any part rolled over into a .log.gz at local midnight.
    latest = logs_dir / "latest.log"
    latest_text = ""
    for gz in sorted(logs_dir.glob("*.log.gz")) if logs_dir.is_dir() else []:
        if gz.name not in gz_before:
            with gzip.open(gz, "rt", encoding="utf-8", errors="replace") as f:
                part = f.read()
            if part != old_latest_text:  # skip the previous run's log rotated at startup
                latest_text += part
                shutil.copyfile(gz, out_dir / gz.name)
    if latest.is_file() and latest.stat().st_mtime != old_latest_mtime:
        latest_text += latest.read_text(encoding="utf-8", errors="replace")
        shutil.copyfile(latest, out_dir / "latest.log")
    elif game_started(srv.lines):
        result["codes"].append(EXIT_INFRA)
        result["infra"] = (result.get("infra", "") + "; run-headless/logs/latest.log was not written").lstrip("; ")
    new_crashes = sorted(crash_reports() - crashes_before)
    if new_crashes:
        result["codes"].append(EXIT_ERROR)
        result["crash_reports"] = new_crashes
        for name in new_crashes:
            shutil.copyfile(RUN_DIR / "crash-reports" / name, out_dir / name)

    baseline_path = HEADLESS_DIR / "baselines" / f"{profile.get('baseline', profile_name)}.warn.txt"
    result["analysis"] = analyze(latest_text, srv.lines, read_baseline(baseline_path),
                                 read_patterns(HEADLESS_DIR / "warn_ignore.txt"),
                                 read_patterns(HEADLESS_DIR / "error_allowlist.txt"))
    a = result["analysis"]
    if a["errors"]:
        result["codes"].append(EXIT_ERROR)
    if (a["new_warns"] and not args.update_baseline) or a["spam_warns"] or a["spam_total"]:
        result["codes"].append(EXIT_WARN)

    collected = {}
    for pattern in profile.get("collect", []):
        for p in sorted(RUN_DIR.glob(pattern)):
            if p.is_file():
                rel = p.relative_to(RUN_DIR).as_posix()
                dest = out_dir / "collected" / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(p, dest)
                collected[rel] = ks.sha256_file(p)
    result["collected"] = collected
    result["timings"]["total_s"] = round(time.monotonic() - t0, 1)
    return result


def update_baseline(path: Path, results: list[dict]) -> None:
    existing = read_baseline(path)
    keys = set(existing)
    for r in results:
        keys |= set(r.get("analysis", {}).get("warn_counts", {}))
    header = ("# WARN baseline for tools/headless_test.py (normalized: numbers -> #, paths -> <ROOT>/<HOME>).\n"
              "# Every line must be explained in the milestone report. --update-baseline only ADDS lines from\n"
              "# passing runs; to rebuild from scratch delete this file and run --update-baseline again.\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(header + "".join(k + "\n" for k in sorted(keys)), encoding="utf-8", newline="\n")
    log(f"baseline {path.relative_to(ks.ROOT)}: {len(existing)} -> {len(keys)} entries")


def summary_md(results: list[dict], final_code: int) -> str:
    lines = [f"# Headless test — exit {final_code}", ""]
    for i, r in enumerate(results, 1):
        a = r.get("analysis", {})
        lines += [f"## Run {i} ({r['profile']})", "",
                  f"- codes: {r['codes'] or '[0]'}; gradle exit {r.get('gradle_exit')}",
                  f"- timings: {r.get('timings')}"]
        if r.get("infra"):
            lines.append(f"- infra: {r['infra']}")
        for c in r.get("commands", []):
            lines.append(f"- `{c['send']}` → {c['status']} ({c['seconds']}s) {c.get('matched') or ''}")
        lines.append(f"- ERROR: {len(a.get('errors', []))} (allowlisted {len(a.get('allowed_errors', []))}); "
                     f"WARN total {a.get('warn_total')}; new WARN {len(a.get('new_warns', []))}; "
                     f"spam {a.get('spam_warns')}")
        for e in a.get("errors", []):
            lines.append(f"  - ERROR `{e['line']}`")
        for w in a.get("new_warns", []):
            lines.append(f"  - new WARN `{w}`")
        if r.get("collected"):
            lines.append(f"- collected: {len(r['collected'])} files")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    ks.setup_stdio()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", default="vanilla")
    ap.add_argument("--fresh", action="store_true", help="delete the profile's world before starting")
    ap.add_argument("--repeat", type=int, default=1, help="run N times; compare collected files")
    ap.add_argument("--seed", help="override the profile seed (world folder gets a _s<seed> suffix)")
    ap.add_argument("--update-baseline", action="store_true", help="merge observed WARN lines into the baseline")
    ap.add_argument("--timeout-start", type=float, default=900.0, help="seconds to wait for 'Done ('")
    ap.add_argument("--gradle-arg", action="append", default=[],
                    help="extra Gradle argument, e.g. --gradle-arg=--no-configuration-cache (repeatable)")
    args = ap.parse_args()
    if args.repeat < 1:
        ap.error("--repeat must be >= 1")

    profiles = tomllib.loads((HEADLESS_DIR / "profiles.toml").read_text(encoding="utf-8"))
    if args.profile not in profiles:
        log(f"ERROR: unknown profile {args.profile!r}; known: {sorted(profiles)}")
        return EXIT_INFRA
    profile = dict(profiles[args.profile])
    if args.seed is not None:
        profile["seed"] = args.seed
        profile["level_name"] = f"{profile['level_name']}_s{args.seed}"
    # "{seed}" in a command or expectation stands for the effective seed
    seed = str(profile["seed"])

    def fill(key, value):
        if not isinstance(value, str):
            return value
        return value.replace("{seed}", re.escape(seed) if key == "expect" else seed)

    profile["commands"] = [{k: fill(k, v) for k, v in c.items()} for c in profile.get("commands", [])]
    java_home = ks.find_java_home(25)
    gen = subprocess.run([sys.executable, str(ks.ROOT / "tools" / "gen_worldgen.py"), "--check"],
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    if gen.returncode != 0:
        log("ERROR: generated worldgen files are stale or the vanilla export is missing:\n" + gen.stdout + gen.stderr)
        return EXIT_INFRA

    stamp =dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    out_root = RESULTS_DIR / f"{stamp}-{args.profile}"
    results = []
    with RunLock(RUN_DIR / ".headless.lock"):
        for i in range(args.repeat):
            out_dir = out_root / f"run{i + 1}" if args.repeat > 1 else out_root
            log(f"run {i + 1}/{args.repeat}: profile {args.profile}, seed {profile['seed']}, "
                f"level-type {profile['level_type']}{', fresh world' if args.fresh else ''}")
            results.append(run_once(args.profile, profile, args, java_home, out_dir))

    codes = {c for r in results for c in r["codes"]}
    if args.repeat > 1:
        first = results[0].get("collected", {})
        if any(r.get("collected", {}) != first for r in results[1:]):
            codes.add(EXIT_NONDET)
            log("ERROR: collected files differ between runs")
    if args.update_baseline:
        if codes - {EXIT_OK, EXIT_WARN}:
            log("ERROR: not updating the WARN baseline: the run failed for another reason")
        else:
            update_baseline(HEADLESS_DIR / "baselines" / f"{profile.get('baseline', args.profile)}.warn.txt",
                            results)
    final = next((c for c in SEVERITY if c in codes), EXIT_OK)

    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / "report.json").write_text(
        json.dumps({"exit": final, "args": vars(args), "runs": results}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8")
    (out_root / "summary.md").write_text(summary_md(results, final), encoding="utf-8")
    for r in results:
        a = r.get("analysis", {})
        for e in a.get("errors", []):
            log(f"ERROR line: {e['line']}")
        for w in a.get("new_warns", []):
            log(f"new WARN: {w}")
    log(f"result: exit {final} -> {out_root.relative_to(ks.ROOT)}")
    return final


if __name__ == "__main__":
    sys.exit(main())
