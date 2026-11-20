from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from conpass import engine as E
from conpass.config import Reservation
from conpass.selectors import load_flow
from conpass.store import Store

from .fake_device import FakeApp, FakeDevice

FIX = Path(__file__).parent / "selectors_fixture.yaml"
TZ = ZoneInfo("Asia/Tokyo")
STORE = "テスト店"


class Clock:
    def __init__(self, t: float):
        self.t = t

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += max(s, 0.001)


def make_app():
    screens = {
        "home": {"home_tab", "search_tab", "history_tab"},
        "search": {"search_field", f"store:{STORE}"},
        "detail": {"store_title", "date:11/25"},
        "confirm": {"submit_btn"},
        "done": {"done_msg"},
        "blank": set(),
        "history": {"history_title"},
        "history_with": {"history_title", f"hist:{STORE}"},
    }
    tr = {
        ("home", "search_tab"): "search",
        ("search", f"store:{STORE}"): "detail",
        ("detail", "date:11/25"): "confirm",
        ("confirm", "submit_btn"): "done",
        ("home", "history_tab"): "history",
    }
    return FakeApp(screens, tr)


@pytest.fixture
def ctx(tmp_path):
    exec_at = datetime(2026, 11, 24, 23, 59, 59)
    clock = Clock(exec_at.replace(tzinfo=TZ).timestamp() - 60)
    res = Reservation(id="t1", store=STORE, visit_date="2026-11-25", execute_at=exec_at, mode="production")
    store = Store(tmp_path / "db.sqlite3")
    app = make_app()
    dev = FakeDevice(app)
    flow = load_flow(FIX)

    def mk(r=res, **kw):
        return E.Engine(dev, flow, r, store, shot_dir=tmp_path / "shots", step_timeout=2,
                        clock=clock.now, sleep=clock.sleep, **kw)
    return dict(res=res, store=store, app=app, dev=dev, flow=flow, mk=mk, clock=clock, exec_at=exec_at)


def test_test_mode_never_taps_submit(ctx):
    r = ctx["res"].model_copy(update={"mode": "test"})
    out = ctx["mk"](r).run()
    assert out.result == E.TEST_OK
    assert "submit_btn" not in ctx["app"].taps
    assert ctx["app"].typed == [STORE]
    assert ctx["store"].get_state(r.dedupe_key()) is None
    assert out.screenshots


def test_production_success_and_no_double_submit(ctx):
    out = ctx["mk"]().run()
    assert out.result == E.SUCCESS, out.detail
    assert ctx["app"].taps.count("submit_btn") == 1
    # tapped at/after target time, never before
    assert out.submit_drift_ms is not None and out.submit_drift_ms >= 0
    assert ctx["clock"].t >= ctx["exec_at"].replace(tzinfo=TZ).timestamp()
    st = ctx["store"].get_state(ctx["res"].dedupe_key())
    assert st["state"] == "succeeded" and st["submit_count"] == 1
    # second run is a no-op
    ctx["app"].current = "home"
    out2 = ctx["mk"]().run()
    assert out2.result == E.SKIPPED_DONE
    assert ctx["app"].taps.count("submit_btn") == 1


def test_login_screen_requires_manual(ctx):
    ctx["app"].screens["home"] = {"home_tab", "login_btn"}
    out = ctx["mk"]().run()
    assert out.result == E.MANUAL and "login" in out.detail
    assert "submit_btn" not in ctx["app"].taps


def test_captcha_before_submit_stops(ctx):
    ctx["app"].screens["confirm"] = {"submit_btn", "captcha_box"}
    out = ctx["mk"]().run()
    assert out.result == E.MANUAL and "captcha" in out.detail
    assert "submit_btn" not in ctx["app"].taps


def test_unclear_result_verified_in_list(ctx):
    app = ctx["app"]
    app.transitions[("confirm", "submit_btn")] = "blank"
    app.transitions[("home", "history_tab")] = "history_with"
    out = ctx["mk"]().run()
    assert out.result == E.SUCCESS_VERIFIED
    assert app.taps.count("submit_btn") == 1
    assert ctx["store"].get_state(ctx["res"].dedupe_key())["state"] == "succeeded"


def test_unclear_and_absent_does_not_resubmit_by_default(ctx):
    ctx["app"].transitions[("confirm", "submit_btn")] = "blank"
    out = ctx["mk"]().run()
    assert out.result == E.FAILED
    assert ctx["app"].taps.count("submit_btn") == 1
    assert ctx["store"].get_state(ctx["res"].dedupe_key())["state"] == "not_found"


def test_resubmit_only_after_list_verified_absent(ctx):
    app = ctx["app"]
    n = {"c": 0}

    def on_tap(v):
        if v == "submit_btn":
            n["c"] += 1
            return "blank" if n["c"] == 1 else "done"
        return None
    app.on_tap = on_tap
    out = ctx["mk"](max_submit_attempts=2).run()
    assert out.result == E.SUCCESS
    assert app.taps.count("submit_btn") == 2
    # list was checked between the two taps
    first, second = [i for i, t in enumerate(app.taps) if t == "submit_btn"]
    assert "history_tab" in app.taps[first:second]


def test_unknown_when_list_unreachable_then_next_run_checks_list_first(ctx):
    app = ctx["app"]
    app.transitions[("confirm", "submit_btn")] = "blank"
    app.screens["home"] = {"home_tab", "search_tab"}  # no history tab -> list check fails
    out = ctx["mk"]().run()
    assert out.result == E.UNKNOWN
    assert ctx["store"].get_state(ctx["res"].dedupe_key())["state"] == "unknown"
    # next run: list available and shows reservation -> success without tapping submit again
    app.screens["home"] = {"home_tab", "search_tab", "history_tab"}
    app.transitions[("home", "history_tab")] = "history_with"
    app.current = "home"
    out2 = ctx["mk"]().run()
    assert out2.result == E.SUCCESS_VERIFIED
    assert app.taps.count("submit_btn") == 1


def test_screen_change_detected_without_submit(ctx):
    ctx["app"].transitions[("home", "search_tab")] = "blank"
    out = ctx["mk"]().run()
    assert out.result == E.SCREEN_CHANGED
    assert "submit_btn" not in ctx["app"].taps
    assert list((ctx["dev"].shots[0].parent).glob("*.xml"))  # page source dumped


def test_transient_network_error_retried(ctx):
    app = ctx["app"]
    n = {"c": 0}

    def on_tap(v):
        if v == "search_tab":
            n["c"] += 1
            return "neterr" if n["c"] == 1 else "search"
        return None
    app.screens["neterr"] = {"net_err"}
    app.on_tap = on_tap
    out = ctx["mk"]().run()
    assert out.result == E.SUCCESS, out.detail


def test_persistent_network_error_gives_up(ctx):
    app = ctx["app"]
    app.screens["neterr"] = {"net_err"}
    app.transitions[("home", "search_tab")] = "neterr"
    out = ctx["mk"]().run()
    assert out.result == E.NETWORK
    assert "submit_btn" not in app.taps


def test_closed_screen(ctx):
    app = ctx["app"]
    app.screens["closedscr"] = {"closed_msg"}
    app.transitions[("confirm", "submit_btn")] = "closedscr"
    assert ctx["mk"]().run().result == E.CLOSED


def test_production_blocked_with_placeholder_selectors(ctx, tmp_path):
    real = load_flow(Path(__file__).parent.parent / "config" / "selectors.yaml")
    assert real.unverified()
    eng = E.Engine(ctx["dev"], real, ctx["res"], ctx["store"], shot_dir=tmp_path)
    out = eng.run()
    assert out.result == E.NOT_READY
    assert ctx["app"].taps == []
