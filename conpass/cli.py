"""Command line interface.

    python -m conpass.cli doctor                 # Appium/device config checks
    python -m conpass.cli screenshot             # Phase 2: grab a screenshot from the iPhone
    python -m conpass.cli recon                  # Phase 3: capture screens for selector building
    python -m conpass.cli probe --job ID         # test selectors against current screen
    python -m conpass.cli validate               # validate YAML configs
    python -m conpass.cli run ID [--test]        # run a job now
    python -m conpass.cli schedule ID            # install launchd agent
    python -m conpass.cli unschedule ID
    python -m conpass.cli state [--clear KEY]    # double-submit guard state
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from datetime import date, datetime

from . import paths


def cmd_doctor(a) -> int:
    from .config import load_device
    from .selectors import load_flow

    ok = True
    cfg = load_device()
    print(f"device.yaml: udid={'set' if cfg.udid else 'MISSING'} bundle_id={cfg.bundle_id or 'MISSING'} "
          f"team={'set' if cfg.xcode_org_id else 'MISSING'}")
    ok &= bool(cfg.udid and cfg.bundle_id)
    try:
        with urllib.request.urlopen(cfg.appium_url.rstrip("/") + "/status", timeout=5) as r:
            st = json.load(r)
        print(f"Appium: up ({st.get('value', {}).get('build', {}).get('version', '?')})")
    except Exception as e:  # noqa: BLE001
        print(f"Appium: NOT reachable at {cfg.appium_url} ({e}) -> run scripts/start_appium.sh")
        ok = False
    try:
        flow = load_flow()
        missing = flow.unverified()
        print(f"selectors.yaml: {len(missing)} unverified/missing locators"
              + (" (production runs blocked)" if missing else " (ready)"))
        for m in missing[:30]:
            print("   -", m)
    except Exception as e:  # noqa: BLE001
        print(f"selectors.yaml: INVALID ({e})")
        ok = False
    return 0 if ok else 1


def cmd_screenshot(a) -> int:
    from .device import connect
    paths.ensure_dirs()
    d = connect()
    try:
        p = paths.RECON_DIR / f"screenshot-{datetime.now().strftime('%Y%m%d-%H%M%S')}.png"
        d.screenshot(p)
        print("saved", p, "app_state", d.app_state())
    finally:
        d.quit()
    return 0


def cmd_recon(a) -> int:
    from .device import connect
    from .recon import capture, interactive
    d = connect()
    try:
        if a.label:
            capture(d, a.label)
        else:
            d.activate_app()
            interactive(d)
    finally:
        d.quit()
    return 0


def cmd_probe(a) -> int:
    from .config import get_reservation
    from .device import connect
    from .recon import probe
    from .selectors import load_flow
    if a.job:
        r = get_reservation(a.job)
        store, vd = r.store, r.visit_date
    else:
        store, vd = a.store or "", date.fromisoformat(a.date) if a.date else date.today()
    d = connect()
    try:
        print(json.dumps(probe(d, load_flow(), store, vd), ensure_ascii=False, indent=1))
    finally:
        d.quit()
    return 0


def cmd_validate(a) -> int:
    from .config import load_device, load_reservations
    from .selectors import load_flow
    rf = load_reservations()
    for r in rf.reservations:
        print(f"OK reservation {r.id}: {r.store} {r.visit_date} @ {r.execute_at} {r.timezone} [{r.mode}]"
              f"{' (past)' if not r.is_future() else ''}")
    flow = load_flow()
    flow.submit_index()
    print(f"OK selectors.yaml: {len(flow.screens)} screens, {len(flow.steps)} steps, "
          f"{len(flow.unverified())} unverified")
    load_device()
    print("OK device.yaml")
    return 0


def cmd_run(a) -> int:
    from .runner import run_job
    out = run_job(a.job, scheduled=a.scheduled, mode_override="test" if a.test else None)
    print(f"{out.result}: {out.detail}")
    return 0 if out.ok or out.result == "not_due" else 2


def cmd_schedule(a) -> int:
    from . import scheduler
    from .config import get_reservation
    r = get_reservation(a.job)
    p = scheduler.install(r, load=not a.no_load)
    print(f"installed {p}\n launch at {scheduler.launch_time_local(r)} (local) for execute_at {r.execute_at_aware}")
    print(" To wake a sleeping Mac beforehand run:\n   " + scheduler.wake_command(r))
    return 0


def cmd_unschedule(a) -> int:
    from . import scheduler
    print("removed" if scheduler.uninstall(a.job) else "not installed")
    return 0


def cmd_state(a) -> int:
    from .store import Store
    s = Store()
    if a.clear:
        s.clear_state(a.clear)
        print("cleared", a.clear)
    for row in s.all_states():
        print(dict(row))
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="conpass")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor").set_defaults(f=cmd_doctor)
    sub.add_parser("screenshot").set_defaults(f=cmd_screenshot)
    r = sub.add_parser("recon"); r.add_argument("--label"); r.set_defaults(f=cmd_recon)
    pr = sub.add_parser("probe"); pr.add_argument("--job"); pr.add_argument("--store"); pr.add_argument("--date")
    pr.set_defaults(f=cmd_probe)
    sub.add_parser("validate").set_defaults(f=cmd_validate)
    ru = sub.add_parser("run"); ru.add_argument("job"); ru.add_argument("--scheduled", action="store_true")
    ru.add_argument("--test", action="store_true", help="force test mode (never taps submit)")
    ru.set_defaults(f=cmd_run)
    sc = sub.add_parser("schedule"); sc.add_argument("job"); sc.add_argument("--no-load", action="store_true")
    sc.set_defaults(f=cmd_schedule)
    us = sub.add_parser("unschedule"); us.add_argument("job"); us.set_defaults(f=cmd_unschedule)
    stt = sub.add_parser("state"); stt.add_argument("--clear", metavar="DEDUPE_KEY"); stt.set_defaults(f=cmd_state)
    a = p.parse_args(argv)
    return a.f(a)


if __name__ == "__main__":
    sys.exit(main())
