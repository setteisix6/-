"""Local macOS notifications (no external services, nothing sensitive sent)."""
from __future__ import annotations

import logging
import shutil
import subprocess

log = logging.getLogger("conpass.notify")


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def notify(title: str, message: str, sound: bool = True) -> bool:
    exe = shutil.which("osascript")
    if not exe:
        log.info("notify (no osascript): %s - %s", title, message)
        return False
    script = f'display notification "{_esc(message[:200])}" with title "{_esc(title[:60])}"'
    if sound:
        script += ' sound name "Glass"'
    try:
        subprocess.run([exe, "-e", script], timeout=10, check=False, capture_output=True)
        return True
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("notification failed: %s", e)
        return False
