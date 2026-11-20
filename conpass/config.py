"""Reservation and device configuration (YAML) with validation."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal, Optional
from zoneinfo import ZoneInfo

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from . import paths

Mode = Literal["test", "production"]
JOB_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")


class Reservation(BaseModel):
    id: str
    store: str = Field(min_length=1, max_length=100)
    visit_date: date
    execute_at: datetime  # naive wall-clock time in `timezone`
    timezone: str = "Asia/Tokyo"
    mode: Mode = "test"
    notify: bool = True
    # Reception window as shown in the app for that store/date. Optional, but
    # when given, execute_at must fall inside it.
    accept_from: Optional[datetime] = None
    accept_until: Optional[datetime] = None
    # How long before execute_at launchd should start us (warm up Appium,
    # navigate up to the confirm screen).
    lead_seconds: int = Field(default=180, ge=30, le=1800)
    # If we start later than execute_at + grace (e.g. Mac was asleep), we do
    # NOT submit; the run is recorded as "missed".
    late_grace_seconds: int = Field(default=120, ge=0, le=3600)
    # Submit taps allowed in total. A 2nd tap only ever happens after the
    # reservation list positively showed that the 1st one did not register.
    max_submit_attempts: int = Field(default=1, ge=1, le=2)
    enabled: bool = True

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        if not JOB_ID_RE.match(v):
            raise ValueError("id must be lowercase letters/digits/-/_ (max 41 chars)")
        return v

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except Exception as e:  # ZoneInfoNotFoundError is a KeyError
            raise ValueError(f"unknown timezone: {v}") from e
        return v

    @field_validator("execute_at", "accept_from", "accept_until", mode="before")
    @classmethod
    def _parse_dt(cls, v):
        if isinstance(v, str):
            v = v.strip().replace("T", " ")
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
                try:
                    return datetime.strptime(v, fmt)
                except ValueError:
                    pass
        return v

    @model_validator(mode="after")
    def _check_times(self):
        if self.execute_at.tzinfo is not None:
            self.execute_at = self.execute_at.replace(tzinfo=None)
        if self.execute_at.date() > self.visit_date:
            raise ValueError("execute_at must be on or before visit_date")
        if self.accept_from and self.accept_until and self.accept_from >= self.accept_until:
            raise ValueError("accept_from must be before accept_until")
        if self.accept_from and self.execute_at < self.accept_from:
            raise ValueError(f"execute_at {self.execute_at} is before reception opens ({self.accept_from})")
        if self.accept_until and self.execute_at > self.accept_until:
            raise ValueError(f"execute_at {self.execute_at} is after reception closes ({self.accept_until})")
        return self

    # --- helpers -----------------------------------------------------------
    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def execute_at_aware(self) -> datetime:
        return self.execute_at.replace(tzinfo=self.tz)

    @property
    def start_at_aware(self) -> datetime:
        return self.execute_at_aware - timedelta(seconds=self.lead_seconds)

    def is_future(self, now: Optional[datetime] = None) -> bool:
        now = now or datetime.now(self.tz)
        return self.execute_at_aware > now

    def dedupe_key(self) -> str:
        """Identity of the *real-world* reservation: one per store+visit date."""
        return f"{self.store.strip()}|{self.visit_date.isoformat()}"


class ReservationFile(BaseModel):
    reservations: list[Reservation] = []

    @model_validator(mode="after")
    def _unique(self):
        ids = [r.id for r in self.reservations]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate reservation id")
        prod_keys = [r.dedupe_key() for r in self.reservations if r.mode == "production" and r.enabled]
        if len(prod_keys) != len(set(prod_keys)):
            raise ValueError("two enabled production jobs target the same store and visit_date")
        return self


def load_reservations(path: Path | None = None) -> ReservationFile:
    path = path or paths.RESERVATIONS_FILE
    if not path.exists():
        return ReservationFile()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    # Accept the single-entry form from the spec: `reservation: {...}`
    if "reservation" in raw and "reservations" not in raw:
        entry = dict(raw["reservation"])
        entry.setdefault("id", "default")
        raw = {"reservations": [entry]}
    return ReservationFile.model_validate(raw)


def save_reservations(rf: ReservationFile, path: Path | None = None) -> None:
    path = path or paths.RESERVATIONS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"reservations": []}
    for r in rf.reservations:
        d = r.model_dump(mode="json", exclude_none=True)
        for k in ("execute_at", "accept_from", "accept_until"):
            if k in d:
                d[k] = getattr(r, k).strftime("%Y-%m-%d %H:%M:%S")
        data["reservations"].append(d)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    tmp.replace(path)


def get_reservation(job_id: str, path: Path | None = None) -> Reservation:
    for r in load_reservations(path).reservations:
        if r.id == job_id:
            return r
    raise KeyError(f"reservation '{job_id}' not found")


class DeviceConfig(BaseModel):
    """Appium connection settings. No secrets live here."""
    appium_url: str = "http://127.0.0.1:4723"
    udid: str = ""                 # from `xcrun xctrace list devices` / `idevice_id -l`
    bundle_id: str = ""            # from `ideviceinstaller -l` (verified on device)
    xcode_org_id: str = ""         # Apple Team ID (10 chars) for WDA signing
    xcode_signing_id: str = "Apple Development"
    updated_wda_bundle_id: str = ""  # e.g. com.yourname.WebDriverAgentRunner
    new_command_timeout: int = 300
    wda_launch_timeout_ms: int = 120000
    implicit_wait_s: float = 0
    step_timeout_s: float = 20


def load_device(path: Path | None = None) -> DeviceConfig:
    path = path or paths.DEVICE_FILE
    if not path.exists():
        return DeviceConfig()
    return DeviceConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})
