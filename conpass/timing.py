"""Precise waiting and clock-accuracy measurement."""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
import time
from datetime import datetime
from typing import Callable, Optional

log = logging.getLogger("conpass.timing")


def wait_until(target: datetime, now: Callable[[], float] = time.time,
               sleep: Callable[[float], None] = time.sleep, spin_ms: float = 30.0) -> float:
    """Block until wall-clock `target` (tz-aware). Returns lateness in ms (>=0 late, <0 early).

    Coarse sleeps until ~spin_ms before the deadline, then short sleeps. Never
    busy-spins at 100% CPU for long.
    """
    t = target.timestamp()
    while True:
        remaining = t - now()
        if remaining <= 0:
            break
        if remaining > 2.0:
            sleep(min(remaining - 1.5, 30.0))
        elif remaining > spin_ms / 1000:
            sleep(remaining - spin_ms / 1000)
        else:
            sleep(0.001)
    return (now() - t) * 1000.0


_OFFSET_RE = re.compile(r"([+-]?\d+\.\d+)\s+\+/-")


def measure_clock_offset_ms(server: str = "time.apple.com", timeout: float = 8.0) -> Optional[float]:
    """Return (server - local) offset in ms using macOS `sntp`, or None if unavailable.

    Positive => local clock is behind. This is logged, not applied, so that the
    user can see whether the Mac's clock is trustworthy at execute time.
    """
    exe = shutil.which("sntp")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, server], capture_output=True, text=True, timeout=timeout).stdout
    except (subprocess.TimeoutExpired, OSError):
        return None
    m = _OFFSET_RE.search(out)
    return float(m.group(1)) * 1000.0 if m else None
