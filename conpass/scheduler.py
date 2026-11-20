"""macOS launchd integration (one LaunchAgent per reservation job)."""
from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

from . import paths
from .config import Reservation

LABEL_PREFIX = "local.conpass.job."
AGENTS_DIR = Path(os.environ.get("CONPASS_AGENTS_DIR", Path.home() / "Library/LaunchAgents"))


def label(job_id: str) -> str:
    return LABEL_PREFIX + job_id


def plist_path(job_id: str) -> Path:
    return AGENTS_DIR / f"{label(job_id)}.plist"


def launch_time_local(res: Reservation) -> datetime:
    """Start time converted to the Mac's local timezone (launchd uses local time).

    launchd only has minute resolution, so we start at the beginning of the
    minute that is at least `lead_seconds` before execute_at.
    """
    start = res.start_at_aware.astimezone()  # system local tz
    return start.replace(second=0, microsecond=0)


def build_plist(res: Reservation, python: str | None = None) -> dict:
    t = launch_time_local(res)
    log_dir = paths.LOG_DIR
    return {
        "Label": label(res.id),
        "ProgramArguments": [python or sys.executable, "-m", "conpass.cli", "run", res.id, "--scheduled"],
        "WorkingDirectory": str(paths.ROOT),
        "EnvironmentVariables": {
            "PATH": "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin",
            "CONPASS_ROOT": str(paths.ROOT),
            "PYTHONUNBUFFERED": "1",
        },
        # No Year key exists in launchd; the runner ignores firings in other years
        # and unloads the agent after its run.
        "StartCalendarInterval": {"Month": t.month, "Day": t.day, "Hour": t.hour, "Minute": t.minute},
        "RunAtLoad": False,
        "ProcessType": "Interactive",       # avoid App Nap / timer coalescing throttling
        "LowPriorityIO": False,
        "StandardOutPath": str(log_dir / f"launchd-{res.id}.out.log"),
        "StandardErrorPath": str(log_dir / f"launchd-{res.id}.err.log"),
    }


def _launchctl(*args: str) -> subprocess.CompletedProcess:
    exe = shutil.which("launchctl")
    if not exe:
        raise RuntimeError("launchctl not found (this only works on macOS)")
    return subprocess.run([exe, *args], capture_output=True, text=True)


def install(res: Reservation, load: bool = True) -> Path:
    if not res.is_future():
        raise ValueError("execute_at is in the past")
    paths.ensure_dirs()
    AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    p = plist_path(res.id)
    if load and p.exists():
        uninstall(res.id)
    with open(p, "wb") as fh:
        plistlib.dump(build_plist(res), fh)
    if load:
        r = _launchctl("bootstrap", f"gui/{os.getuid()}", str(p))
        if r.returncode != 0 and "already" not in (r.stderr or "").lower():
            raise RuntimeError(f"launchctl bootstrap failed: {r.stderr.strip()}")
    return p


def uninstall(job_id: str) -> bool:
    p = plist_path(job_id)
    if shutil.which("launchctl"):
        _launchctl("bootout", f"gui/{os.getuid()}/{label(job_id)}")
    if p.exists():
        p.unlink()
        return True
    return False


def is_installed(job_id: str) -> bool:
    return plist_path(job_id).exists()


def wake_command(res: Reservation, minutes_before: int = 3) -> str:
    """`pmset` command that wakes the Mac before the run (needs admin; user runs it)."""
    t = (launch_time_local(res) - timedelta(minutes=minutes_before))
    return f'sudo pmset schedule wakeorpoweron "{t.strftime("%m/%d/%Y %H:%M:%S")}"'
