"""Reservation flow engine.

Safety rules implemented here:
  * Every step before the submit step is "safe" and may be retried (with app
    relaunch) a bounded number of times.
  * The submit step is tapped at most once per attempt. Before the tap, the
    reservation state is persisted as `submitting`; afterwards only an explicit
    completion screen or a positive match in the reservation list counts as
    success.
  * If the outcome is unclear, the reservation list is checked before any
    further submit. A second submit happens only if the list positively shows
    no reservation AND max_submit_attempts allows it.
  * Login / CAPTCHA / unknown screens stop the run and ask for manual action.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from . import paths
from .config import Reservation
from .device import Device, DeviceError
from .selectors import INTERRUPT_SCREENS, FlowSpec, Locator, Step, make_context
from .store import Store
from .timing import wait_until

log = logging.getLogger("conpass.engine")


# Result codes (also stored in DB)
SUCCESS = "success"                    # completion screen seen
SUCCESS_VERIFIED = "success_verified"  # confirmed via reservation list
ALREADY = "already_applied"
TEST_OK = "test_ok"                    # test mode reached the submit step
SKIPPED_DONE = "skipped_already_succeeded"
MANUAL = "manual_required"             # login / captcha / unknown screen
SCREEN_CHANGED = "screen_changed"
CLOSED = "closed"                      # reception closed / full
NETWORK = "network_error"
UNKNOWN = "unknown"                    # submitted, could not confirm
FAILED = "failed"
MISSED = "missed"                      # started too late
NOT_READY = "not_ready"                # selectors unverified for production


class ManualActionRequired(Exception):
    def __init__(self, screen: str, msg: str):
        super().__init__(msg)
        self.screen = screen


class ScreenMismatch(Exception):
    pass


@dataclass
class RunOutcome:
    result: str
    detail: str = ""
    submit_drift_ms: Optional[float] = None
    screenshots: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.result in (SUCCESS, SUCCESS_VERIFIED, ALREADY, TEST_OK, SKIPPED_DONE)


class Engine:
    def __init__(self, device: Device, flow: FlowSpec, res: Reservation, store: Store,
                 shot_dir: Path | None = None, step_timeout: float = 20.0,
                 max_safe_retries: int = 2, max_submit_attempts: int = 1,
                 result_timeout: float = 30.0,
                 clock: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep):
        self.d = device
        self.flow = flow
        self.res = res
        self.store = store
        self.ctx = make_context(res.store, res.visit_date)
        self.shot_dir = shot_dir or (paths.SHOT_DIR / res.id / datetime.now().strftime("%Y%m%d-%H%M%S"))
        self.step_timeout = step_timeout
        self.max_safe_retries = max_safe_retries
        self.max_submit_attempts = max_submit_attempts
        self.result_timeout = result_timeout
        self.clock = clock
        self.sleep = sleep
        self.shots: list[str] = []
        self.key = res.dedupe_key()

    # ------------------------------------------------------------------ utils
    def shot(self, label: str) -> None:
        p = self.shot_dir / f"{len(self.shots):02d}-{label}.png"
        try:
            self.d.screenshot(p)
            self.shots.append(str(p))
            log.info("screenshot %s", p)
        except Exception as e:  # noqa: BLE001
            log.warning("screenshot failed (%s): %s", label, e)

    def dump_source(self, label: str) -> None:
        try:
            p = self.shot_dir / f"{label}.xml"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(self.d.page_source(), encoding="utf-8")
            log.info("page source saved %s", p)
        except Exception as e:  # noqa: BLE001
            log.warning("page source dump failed: %s", e)

    def _loc(self, loc: Locator) -> Locator:
        return loc.render(self.ctx)

    def is_screen(self, name: str) -> bool:
        sc = self.flow.screens.get(name)
        if not sc or not sc.markers:
            return False
        return all(self.d.exists(self._loc(m)) for m in sc.markers)

    def detect_screen(self, candidates: Optional[list[str]] = None) -> Optional[str]:
        names = candidates if candidates is not None else list(self.flow.screens)
        # interrupts first: they win over everything else
        ordered = [n for n in INTERRUPT_SCREENS if n in names] + [n for n in names if n not in INTERRUPT_SCREENS]
        for n in ordered:
            if self.is_screen(n):
                return n
        return None

    def check_interrupts(self) -> None:
        scr = self.detect_screen([n for n in INTERRUPT_SCREENS if n in self.flow.screens])
        if scr is None:
            return
        self.shot(f"interrupt-{scr}")
        if scr == "network_error":
            raise ConnectionError("app shows a network error screen")
        if scr == "system_alert":
            raise ManualActionRequired(scr, "an iOS system alert is shown; please dismiss it manually")
        msgs = {
            "login": "ログインが必要です。iPhoneで777CON-PASSに手動でログインしてください。",
            "captcha": "追加認証/CAPTCHAが表示されています。手動で対応してください（自動回避はしません）。",
            "maintenance": "メンテナンス中の表示です。",
            "force_update": "アプリのアップデートが必要です。App Storeで更新後、セレクタを再検証してください。",
        }
        raise ManualActionRequired(scr, msgs.get(scr, scr))

    def wait_screen(self, names: list[str], timeout: float) -> str:
        end = self.clock() + timeout
        while True:
            self.check_interrupts()
            for n in names:
                if self.is_screen(n):
                    return n
            if self.clock() >= end:
                raise ScreenMismatch(f"expected screen {names} not reached within {timeout:.0f}s")
            self.sleep(0.3)

    # ------------------------------------------------------------- steps
    def run_step(self, st: Step) -> Optional[str]:
        timeout = st.timeout_s or self.step_timeout
        loc = self._loc(st.target) if st.target else None
        log.info("step %s (%s) %s", st.name, st.action, f"{loc.by}={loc.value}" if loc else "")
        if st.optional and loc is not None and not self.d.wait_for(loc, min(timeout, 3)):
            log.info("optional step %s skipped (target absent)", st.name)
            return None
        if st.action == "tap":
            self.d.tap(loc, timeout)
        elif st.action in ("type", "clear_and_type"):
            self.d.type_text(loc, st.text.format(**self.ctx), timeout, clear=st.action == "clear_and_type")
        elif st.action == "scroll_to_tap":
            self.d.scroll_to_tap(loc, timeout)
        elif st.action == "wait":
            if not self.d.wait_for(loc, timeout):
                raise ScreenMismatch(f"wait target not found: {loc.value}")
        elif st.action == "back":
            self.d.back()
        elif st.action == "pause":
            self.sleep(st.seconds)
        expects = ([st.expect] if st.expect else []) + list(st.expect_any)
        if expects:
            return self.wait_screen(expects, timeout)
        self.check_interrupts()
        return None

    def go_home(self) -> None:
        """(Re)launch app and wait for the home screen."""
        try:
            if self.d.app_state() != 4:
                self.d.activate_app()
        except DeviceError:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("app_state failed: %s", e)
            self.d.activate_app()
        self.wait_screen([self.flow.home_screen], self.step_timeout * 2)

    def relaunch(self) -> None:
        log.info("relaunching app")
        try:
            self.d.terminate_app()
        except Exception as e:  # noqa: BLE001
            log.warning("terminate failed: %s", e)
        self.sleep(1.0)
        self.d.activate_app()
        self.wait_screen([self.flow.home_screen], self.step_timeout * 2)

    def navigate_to_submit(self) -> None:
        """Run all safe steps up to (not including) the submit step, with retries."""
        idx = self.flow.submit_index()
        attempt = 0
        while True:
            try:
                self.go_home() if attempt == 0 else self.relaunch()
                self.shot("home")
                for st in self.flow.steps[:idx]:
                    self.run_step(st)
                self.shot("before-submit")
                return
            except ManualActionRequired:
                raise
            except (ScreenMismatch, LookupError, ConnectionError) as e:
                attempt += 1
                self.shot(f"retry{attempt}")
                self.dump_source(f"retry{attempt}")
                log.warning("navigation failed (%s), attempt %d/%d", e, attempt, self.max_safe_retries)
                if attempt > self.max_safe_retries:
                    raise
                self.sleep(min(2 ** attempt, 10))  # gentle backoff, no hammering

    # ------------------------------------------------------- reservation list
    def check_reservation_list(self) -> Optional[bool]:
        """True = reservation exists, False = verified absent, None = could not tell."""
        vl = self.flow.verify_list
        if not vl.match or not vl.steps:
            log.warning("verify_list not configured; cannot check reservation list")
            return None
        try:
            self.relaunch()
            for st in vl.steps:
                self.run_step(st)
            self.wait_screen([vl.screen], self.step_timeout)
            found = self.d.wait_for(self._loc(vl.match), 5)
            self.shot("reservation-list")
            log.info("reservation list check: %s", "FOUND" if found else "not found")
            return found
        except ManualActionRequired:
            raise
        except Exception as e:  # noqa: BLE001
            self.shot("reservation-list-failed")
            self.dump_source("reservation-list-failed")
            log.warning("reservation list check failed: %s", e)
            return None

    # ------------------------------------------------------------------ main
    def run(self) -> RunOutcome:
        try:
            return self._run()
        except ManualActionRequired as e:
            return RunOutcome(MANUAL, f"[{e.screen}] {e}", screenshots=self.shots)
        except DeviceError as e:
            return RunOutcome(FAILED, f"device/Appium error: {e}", screenshots=self.shots)
        except ScreenMismatch as e:
            self.dump_source("screen-changed")
            return RunOutcome(SCREEN_CHANGED, f"{e} — アプリ画面が変わった可能性があります。recon で再確認してください。",
                              screenshots=self.shots)
        except ConnectionError as e:
            return RunOutcome(NETWORK, str(e), screenshots=self.shots)
        except LookupError as e:
            self.dump_source("element-missing")
            return RunOutcome(SCREEN_CHANGED, str(e), screenshots=self.shots)

    def _run(self) -> RunOutcome:
        prod = self.res.mode == "production"
        if prod:
            missing = self.flow.unverified()
            if missing:
                return RunOutcome(NOT_READY, "unverified selectors: " + ", ".join(missing[:20]))

        # ---- double-submit guard -------------------------------------------
        st = self.store.get_state(self.key) if prod else None
        submits_done = st["submit_count"] if st else 0
        if st and st["state"] == "succeeded":
            return RunOutcome(SKIPPED_DONE, f"already succeeded at {st['updated_at']}")
        if st and st["state"] in ("submitting", "unknown"):
            log.warning("previous run ended in state %s; checking reservation list first", st["state"])
            found = self.check_reservation_list()
            if found:
                self.store.set_state(self.key, self.res.id, "succeeded", "confirmed by list (previous run)")
                return RunOutcome(SUCCESS_VERIFIED, "previous submit confirmed in reservation list",
                                  screenshots=self.shots)
            if found is None:
                return RunOutcome(UNKNOWN, "previous result unknown and list could not be checked; "
                                           "手動で予約一覧を確認してください", screenshots=self.shots)
            self.store.set_state(self.key, self.res.id, "not_found", "list verified absent")

        # ---- navigate (safe, retryable) --------------------------------------
        self.navigate_to_submit()
        submit = self.flow.steps[self.flow.submit_index()]

        if not prod:
            return RunOutcome(TEST_OK, "test mode: reached submit step, did NOT tap", screenshots=self.shots)

        # ---- submit (irreversible) ------------------------------------------
        while True:
            if submits_done >= self.max_submit_attempts:
                return RunOutcome(FAILED, f"submit attempts exhausted ({submits_done})", screenshots=self.shots)
            drift = wait_until(self.res.execute_at_aware, now=self.clock, sleep=self.sleep)
            self.check_interrupts()
            self.store.set_state(self.key, self.res.id, "submitting", f"drift {drift:.1f}ms", bump_submit=True)
            submits_done += 1
            log.info("SUBMIT tap (drift %.1f ms)", drift)
            try:
                self.d.tap(self._loc(submit.target), 5)
            except LookupError as e:
                # Tap did not happen: nothing was sent. Safe to report as failure.
                self.store.set_state(self.key, self.res.id, "not_found", f"submit button missing: {e}")
                self.dump_source("submit-missing")
                return RunOutcome(SCREEN_CHANGED, f"submit button not found: {e}", drift, self.shots)
            except Exception as e:  # noqa: BLE001 - tap may or may not have registered
                log.error("error during submit tap: %s", e)

            result_screens = list(submit.expect_any or ([submit.expect] if submit.expect else [])) or \
                ["complete", "already_applied", "closed", "submit_error"]
            screen = None
            try:
                screen = self.wait_screen([s for s in result_screens if s in self.flow.screens],
                                          submit.timeout_s or self.result_timeout)
            except (ScreenMismatch, ConnectionError) as e:
                log.warning("no result screen after submit: %s", e)
            except ManualActionRequired as e:
                # e.g. CAPTCHA after submit: we cannot know the result
                self.store.set_state(self.key, self.res.id, "unknown", f"interrupt after submit: {e.screen}")
                raise
            self.shot(f"after-submit-{screen or 'none'}")

            if screen == "complete":
                self.store.set_state(self.key, self.res.id, "succeeded", "completion screen")
                return RunOutcome(SUCCESS, "completion screen confirmed", drift, self.shots)
            if screen == "already_applied":
                self.store.set_state(self.key, self.res.id, "succeeded", "app says already applied")
                return RunOutcome(ALREADY, "app reports reservation already exists", drift, self.shots)
            if screen == "closed":
                self.store.set_state(self.key, self.res.id, "not_found", "reception closed/full")
                return RunOutcome(CLOSED, "reception closed or full", drift, self.shots)

            # Unclear: never treat as success; verify before any re-submit
            self.dump_source("after-submit-unclear")
            self.store.set_state(self.key, self.res.id, "unknown", f"result screen: {screen}")
            found = self.check_reservation_list()
            if found:
                self.store.set_state(self.key, self.res.id, "succeeded", "confirmed by list")
                return RunOutcome(SUCCESS_VERIFIED, "confirmed in reservation list", drift, self.shots)
            if found is None:
                return RunOutcome(UNKNOWN, "申込結果を確認できませんでした。予約一覧を手動で確認してください。",
                                  drift, self.shots)
            self.store.set_state(self.key, self.res.id, "not_found", "list verified absent after submit")
            if submits_done >= self.max_submit_attempts:
                return RunOutcome(FAILED, "submit not reflected in reservation list", drift, self.shots)
            if self.res.accept_until and datetime.now(self.res.tz).replace(tzinfo=None) > self.res.accept_until:
                return RunOutcome(CLOSED, "reception window over", drift, self.shots)
            log.warning("list verified absent; one more submit attempt")
            self.navigate_to_submit()
