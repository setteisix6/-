"""Run one reservation job end-to-end (used by CLI, launchd and the web UI)."""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

from . import paths
from .config import Reservation, get_reservation, load_device
from .engine import MISSED, Engine, RunOutcome, FAILED
from .notify import notify
from .selectors import load_flow
from .store import AlreadyRunning, Store, exclusive_lock
from .timing import measure_clock_offset_ms

log = logging.getLogger("conpass.runner")

NOT_DUE = "not_due"


def setup_logging(job_id: str) -> Path:
    paths.ensure_dirs()
    lp = paths.LOG_DIR / f"{job_id}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.log"
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for h in list(root.handlers):
        if getattr(h, "_conpass", False):
            root.removeHandler(h)
    fmt = logging.Formatter("%(asctime)s.%(msecs)03d %(levelname)s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    fh = logging.FileHandler(lp, encoding="utf-8")
    fh.setFormatter(fmt)
    fh._conpass = True  # type: ignore[attr-defined]
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    sh._conpass = True  # type: ignore[attr-defined]
    root.addHandler(fh)
    root.addHandler(sh)
    return lp


def due_status(res: Reservation, now: datetime, scheduled: bool) -> str:
    """'due', 'early' (launchd fired in another year / too soon) or 'late'."""
    if not scheduled:
        return "late" if (res.mode == "production" and now > res.execute_at_aware +
                          timedelta(seconds=res.late_grace_seconds)) else "due"
    if now < res.start_at_aware - timedelta(minutes=10):
        return "early"
    if now > res.execute_at_aware + timedelta(seconds=res.late_grace_seconds):
        return "late"
    return "due"


class _Caffeinate:
    """Keep the Mac awake for the duration of the run (`caffeinate -dimsu`)."""

    def __enter__(self):
        exe = shutil.which("caffeinate")
        self.p = subprocess.Popen([exe, "-dimsu", "-w", str(os.getpid())]) if exe else None
        return self

    def __exit__(self, *a):
        if self.p:
            self.p.terminate()


def run_job(job_id: str, scheduled: bool = False, mode_override: Optional[str] = None,
            device_factory: Optional[Callable] = None, store: Optional[Store] = None,
            now: Optional[datetime] = None) -> RunOutcome:
    log_path = setup_logging(job_id)
    started_wall = time.time()
    res = get_reservation(job_id)
    if mode_override:
        res = res.model_copy(update={"mode": mode_override})
    store = store or Store()
    now = now or datetime.now(res.tz)

    status = due_status(res, now, scheduled)
    launch_drift = (now - res.start_at_aware).total_seconds() * 1000 if scheduled else None
    log.info("job=%s mode=%s store=%s visit=%s execute_at=%s %s status=%s launch_drift=%s",
             res.id, res.mode, res.store, res.visit_date, res.execute_at, res.timezone, status,
             f"{launch_drift:.0f}ms" if launch_drift is not None else "-")

    if status == "early":
        log.info("not due (launchd fired outside the target date); exiting")
        return RunOutcome(NOT_DUE)
    if not res.enabled:
        log.info("job disabled; exiting")
        return RunOutcome(NOT_DUE, "disabled")

    offset = measure_clock_offset_ms()
    if offset is not None:
        log.info("clock offset vs time.apple.com: %+.1f ms", offset)
        if abs(offset) > 500:
            log.warning("Mac clock is off by %.0f ms; run `sudo sntp -sS time.apple.com`", offset)

    run_id = store.start_run(res.id, res.mode, res.execute_at_aware.isoformat(), str(log_path),
                             launch_drift, offset)
    outcome: RunOutcome
    try:
        if status == "late":
            outcome = RunOutcome(MISSED, f"started {now.isoformat()} — after execute_at + grace; did not submit "
                                         "(Macがスリープしていた可能性があります)")
        else:
            with exclusive_lock("device"), _Caffeinate():
                outcome = _execute(res, store, device_factory)
    except AlreadyRunning as e:
        outcome = RunOutcome(FAILED, f"another run is active: {e}")
    except Exception as e:  # noqa: BLE001
        log.exception("unexpected error")
        outcome = RunOutcome(FAILED, f"unexpected: {e!r}")

    store.finish_run(run_id, outcome.result, outcome.detail, outcome.submit_drift_ms, outcome.screenshots)
    log.info("RESULT %s: %s (submit drift %s, total %.1fs)", outcome.result, outcome.detail,
             f"{outcome.submit_drift_ms:.1f}ms" if outcome.submit_drift_ms is not None else "-",
             time.time() - started_wall)

    if res.notify:
        notify(f"777CON-PASS {'✅' if outcome.ok else '⚠️'} {res.mode}",
               f"{res.store} {res.visit_date}: {outcome.result} {outcome.detail}"[:200])

    if scheduled:
        from . import scheduler
        try:
            scheduler.uninstall(res.id)   # one-shot: don't fire again next year
        except Exception as e:  # noqa: BLE001
            log.warning("could not unload launchd job: %s", e)
    return outcome


def _execute(res: Reservation, store: Store, device_factory) -> RunOutcome:
    from .device import DeviceError, connect

    flow = load_flow()
    dev_cfg = load_device()
    factory = device_factory or (lambda: connect(dev_cfg))
    dev = None
    for attempt in range(1, 4):   # session creation is side-effect free -> safe to retry
        try:
            dev = factory()
            break
        except DeviceError as e:
            log.warning("device connect failed (%d/3): %s", attempt, e)
            if attempt == 3:
                return RunOutcome(FAILED, f"could not connect to iPhone/Appium: {e}")
            time.sleep(5 * attempt)
    try:
        eng = Engine(dev, flow, res, store, step_timeout=dev_cfg.step_timeout_s,
                     max_submit_attempts=res.max_submit_attempts)
        return eng.run()
    finally:
        dev.quit()
