import plistlib
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from conpass import paths
from conpass.config import Reservation, ReservationFile, load_reservations, save_reservations
from conpass.timing import wait_until

TZ = ZoneInfo("Asia/Tokyo")


def R(**kw):
    base = dict(id="a", store="店", visit_date="2026-11-25", execute_at="2026-11-24 23:59:59")
    base.update(kw)
    return Reservation.model_validate(base)


def test_spec_example_single_form(tmp_path):
    p = tmp_path / "r.yaml"
    p.write_text('reservation:\n  store: "店舗名"\n  visit_date: "2026-11-25"\n'
                 '  execute_at: "2026-11-24 23:59:59"\n  timezone: "Asia/Tokyo"\n  mode: "test"\n',
                 encoding="utf-8")
    rf = load_reservations(p)
    r = rf.reservations[0]
    assert r.id == "default" and r.execute_at == datetime(2026, 11, 24, 23, 59, 59)
    assert r.execute_at_aware.utcoffset() == timedelta(hours=9)


def test_validation_rules():
    with pytest.raises(ValidationError):
        R(execute_at="2026-11-26 00:00:00")          # after visit date
    with pytest.raises(ValidationError):
        R(accept_from="2026-11-25 00:00:00")          # before reception opens
    with pytest.raises(ValidationError):
        R(accept_until="2026-11-24 12:00:00")         # after reception closes
    with pytest.raises(ValidationError):
        R(timezone="Mars/Base")
    with pytest.raises(ValidationError):
        R(id="Bad ID")
    R(accept_from="2026-11-24 12:00:00", accept_until="2026-11-25 08:00:00")
    with pytest.raises(ValidationError):
        ReservationFile(reservations=[R(id="a", mode="production"), R(id="b", mode="production")])
    ReservationFile(reservations=[R(id="a", mode="production"), R(id="b", mode="test")])


def test_roundtrip(tmp_path):
    p = tmp_path / "r.yaml"
    save_reservations(ReservationFile(reservations=[R(accept_until="2026-11-25 08:00")]), p)
    r = load_reservations(p).reservations[0]
    assert r.accept_until == datetime(2026, 11, 25, 8, 0)


def test_wait_until_precision():
    target = datetime.now(TZ) + timedelta(milliseconds=300)
    drift = wait_until(target)
    assert 0 <= drift < 50


def test_plist(monkeypatch, tmp_path):
    from conpass import scheduler
    monkeypatch.setattr(scheduler, "AGENTS_DIR", tmp_path)
    r = R(id="job1", execute_at="2026-12-24 23:59:59", visit_date="2026-12-25", lead_seconds=180)
    p = scheduler.install(r, load=False)
    d = plistlib.loads(p.read_bytes())
    assert d["Label"] == "local.conpass.job.job1"
    assert d["ProgramArguments"][-3:] == ["run", "job1", "--scheduled"]
    local = (r.execute_at_aware - timedelta(seconds=180)).astimezone()
    sci = d["StartCalendarInterval"]
    assert (sci["Month"], sci["Day"], sci["Hour"], sci["Minute"]) == (local.month, local.day, local.hour, local.minute)
    assert "pmset schedule wakeorpoweron" in scheduler.wake_command(r)
    assert scheduler.uninstall("job1") and not p.exists()


def test_runner_late_start_is_missed_and_never_submits(monkeypatch, tmp_path):
    from conpass import runner
    from conpass.store import Store
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    monkeypatch.setattr(paths, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(paths, "SHOT_DIR", tmp_path / "s")
    monkeypatch.setattr(paths, "RECON_DIR", tmp_path / "r")
    rp = tmp_path / "res.yaml"
    monkeypatch.setattr(paths, "RESERVATIONS_FILE", rp)
    monkeypatch.setattr(runner, "measure_clock_offset_ms", lambda: None)
    monkeypatch.setattr(runner, "notify", lambda *a, **k: None)
    save_reservations(ReservationFile(reservations=[R(id="late", mode="production")]), rp)
    called = []
    st = Store(tmp_path / "db")
    now = datetime(2026, 11, 25, 0, 10, tzinfo=TZ)  # 10 min after execute_at (Mac was asleep)
    out = runner.run_job("late", scheduled=False, device_factory=lambda: called.append(1),
                         store=st, now=now)
    assert out.result == "missed" and not called
    assert st.runs("late")[0]["result"] == "missed"
    # a stale launchd firing much later is recorded as missed, never submitted
    out = runner.run_job("late", scheduled=True, store=st, now=datetime(2027, 11, 24, 23, 57, tzinfo=TZ))
    assert out.result == "missed"


def test_due_status():
    from conpass.runner import due_status
    r = R(lead_seconds=180)
    base = r.execute_at_aware
    assert due_status(r, base - timedelta(days=365), True) == "early"
    assert due_status(r, base - timedelta(seconds=180), True) == "due"
    assert due_status(r, base + timedelta(seconds=60), True) == "due"
    assert due_status(r, base + timedelta(seconds=600), True) == "late"


@pytest.fixture
def client(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from conpass import scheduler
    from conpass.web import app as webapp
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    monkeypatch.setattr(paths, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(paths, "DB_PATH", tmp_path / "db")
    monkeypatch.setattr(paths, "RESERVATIONS_FILE", tmp_path / "res.yaml")
    monkeypatch.setattr(scheduler, "AGENTS_DIR", tmp_path / "agents")
    return TestClient(webapp.app, headers={"origin": "http://127.0.0.1:8777"})


def test_web_crud(client, tmp_path):
    assert client.get("/").status_code == 200
    form = dict(id="w1", store="店A", visit_date="2026-12-25", execute_at="2026-12-24T23:59:59",
                timezone="Asia/Tokyo", mode="test", notify="1", lead_seconds="180")
    r = client.post("/jobs", data=form, follow_redirects=False)
    assert r.status_code == 303 and "w1" in r.headers["location"]
    assert load_reservations(tmp_path / "res.yaml").reservations[0].store == "店A"
    page = client.get("/").text
    assert "店A" in page and "テスト実行" in page
    # production needs explicit confirmation
    client.post("/jobs", data={**form, "mode": "production"})
    assert load_reservations(tmp_path / "res.yaml").reservations[0].mode == "test"
    client.post("/jobs", data={**form, "mode": "production", "confirm_production": "yes"})
    assert load_reservations(tmp_path / "res.yaml").reservations[0].mode == "production"
    # invalid input is rejected
    client.post("/jobs", data={**form, "id": "w2", "execute_at": "2026-12-26T00:00:00"})
    assert len(load_reservations(tmp_path / "res.yaml").reservations) == 1
    client.post("/jobs/w1/delete")
    assert load_reservations(tmp_path / "res.yaml").reservations == []


def test_web_blocks_cross_origin(client):
    r = client.post("/jobs/x/delete", headers={"origin": "https://evil.example"})
    assert r.status_code == 403


def test_web_file_traversal_blocked(client):
    assert client.get("/files/../../etc/passwd").status_code == 404
