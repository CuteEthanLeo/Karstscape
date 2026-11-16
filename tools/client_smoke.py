"""Time-boxed runClient smoke check (no screen needed).

  python tools/client_smoke.py

Starts `gradlew runClient`, waits for these log markers in order, then closes the game:
  1. Fabric Loader loads Minecraft 26.3
  2. karstscape is in the mod list
  3. "Karstscape initialized"
  4. "Backend library:"      (renderer up)
  5. "Created: ... blocks.png-atlas"  (resources loaded, title screen reached)
After the last marker it waits SETTLE seconds for crashes, then asks the window to close (taskkill without /F or /T,
which the game handles as a normal quit; "Stopping!" is expected), forcing only if that fails.

ERROR/WARN lines from run/logs/latest.log are listed but do not fail the check: an offline dev client
logs expected profile/Realms/telemetry errors. Result: build/client-smoke/<UTC time>/ (exit 0 = pass).
"""
from __future__ import annotations

import datetime as dt
import json
import re
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import headless_test as ht  # noqa: E402
import ks_common as ks  # noqa: E402

RUN_DIR = ks.ROOT / "run"
TIMEOUT = 900
SETTLE = 20
MARKERS = [
    ("loader", re.compile(r"Loading Minecraft 26\.3 with Fabric Loader 0\.20\.1")),
    ("modlist", re.compile(r"^\s*- karstscape \S+")),
    ("karstscape", re.compile(r"Karstscape initialized")),
    ("renderer", re.compile(r"Backend library: ")),
    ("atlas", re.compile(r"Created: \d+x\d+x\d+ minecraft:textures/atlas/blocks\.png-atlas")),
]
CRASH_RE = re.compile(r"---- Minecraft Crash Report ----|Crash report saved to|BUILD FAILED|#@!@# Game crashed")


def log(msg: str) -> None:
    print(f"[client] {msg}", flush=True)


def main() -> int:
    ks.setup_stdio()
    java_home = ks.find_java_home(25)
    out = ks.ROOT / "build" / "client-smoke" / dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    out.mkdir(parents=True, exist_ok=True)
    crashes_before = {p.name for p in (RUN_DIR / "crash-reports").glob("*")} if (RUN_DIR / "crash-reports").is_dir() else set()

    result = {"markers": {}, "pass": False}
    t0 = time.monotonic()
    game = ht.ServerProcess(java_home, out / "console.log", [], task="runClient")
    try:
        idx = 0
        for name, pat in MARKERS:
            remaining = TIMEOUT - (time.monotonic() - t0)
            st, found = game.wait_for(pat, idx, max(remaining, 1), CRASH_RE)
            if st != "ok":
                result["failure"] = f"marker '{name}' -> {st}"
                log("FAIL: " + result["failure"])
                break
            idx = found
            result["markers"][name] = {"t_s": round(time.monotonic() - t0, 1), "line": game.lines[found]}
            log(f"marker {name} at {result['markers'][name]['t_s']}s: {game.lines[found]}")
        else:
            st, _ = game.wait_for(CRASH_RE, idx, SETTLE)
            if st == "ok":
                result["failure"] = "crash after resources loaded"
                log("FAIL: " + result["failure"])
            else:
                result["pass"] = True
                log(f"no crash within {SETTLE}s after resources loaded")
    finally:
        if game.proc.poll() is None:
            # Ask only the game JVM (not cmd/Gradle) to close its window: taskkill without /F sends
            # WM_CLOSE, which Minecraft treats like clicking the window's close button.
            game_pids = ks.find_java_pids("-Dfabric.dli.env=client", str(ks.ROOT))
            log(f"closing the game window (normal quit), game pid(s) {game_pids}")
            for pid in game_pids:
                ks.request_close(pid)
            st, _ = game.wait_for(re.compile(r"Stopping!"), 0, 60)
            result["clean_quit"] = st == "ok"
            if not game.wait_exit(60):
                log("game did not exit; forcing")
                game.kill()
        game.close()

    latest = RUN_DIR / "logs" / "latest.log"
    if latest.is_file():
        shutil.copyfile(latest, out / "latest.log")
        entries = ht.parse_log(latest.read_text(encoding="utf-8", errors="replace"))
        result["errors"] = [ht.entry_key(e) for e in entries if e["level"] in ("ERROR", "FATAL")]
        result["warns"] = sorted({ht.entry_key(e) for e in entries if e["level"] == "WARN"})
    new_crashes = sorted({p.name for p in (RUN_DIR / "crash-reports").glob("*")} - crashes_before) \
        if (RUN_DIR / "crash-reports").is_dir() else []
    if new_crashes:
        result["pass"] = False
        result["crash_reports"] = new_crashes
    result["total_s"] = round(time.monotonic() - t0, 1)
    (out / "report.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    log(f"ERROR lines: {len(result.get('errors', []))}, distinct WARN: {len(result.get('warns', []))}, "
        f"clean quit: {result.get('clean_quit')}")
    log(f"result: {'PASS' if result['pass'] else 'FAIL'} -> {out.relative_to(ks.ROOT)}")
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
