"""SQLite persistence: per-reservation state (double-submit guard) and run log."""
from __future__ import annotations

import contextlib
import fcntl
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

from . import paths

# Per real-world reservation (store|visit_date):
#   submitting  - the submit tap is about to happen / happened, result not yet known
#   succeeded   - completion screen or reservation list confirmed
#   unknown     - tapped but could not confirm either way -> must verify list first
#   not_found   - list verified that no reservation exists (safe to submit again)
STATES = ("submitting", "succeeded", "unknown", "not_found")

SCHEMA = """
CREATE TABLE IF NOT EXISTS reservation_state (
    dedupe_key TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    state TEXT NOT NULL,
    submit_count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    detail TEXT
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    mode TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    result TEXT,
    detail TEXT,
    target_at TEXT,
    submit_drift_ms REAL,
    launch_drift_ms REAL,
    clock_offset_ms REAL,
    log_path TEXT,
    screenshots TEXT
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class Store:
    def __init__(self, db_path: Path | None = None):
        self.path = db_path or paths.DB_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, isolation_level=None, timeout=10)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)

    # -- reservation state --------------------------------------------------
    def get_state(self, key: str) -> Optional[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM reservation_state WHERE dedupe_key=?", (key,)).fetchone()

    def set_state(self, key: str, job_id: str, state: str, detail: str = "", bump_submit: bool = False) -> None:
        assert state in STATES, state
        self.conn.execute(
            """INSERT INTO reservation_state(dedupe_key, job_id, state, submit_count, updated_at, detail)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(dedupe_key) DO UPDATE SET job_id=excluded.job_id, state=excluded.state,
                 submit_count=reservation_state.submit_count + ?, updated_at=excluded.updated_at,
                 detail=excluded.detail""",
            (key, job_id, state, 1 if bump_submit else 0, _now(), detail, 1 if bump_submit else 0),
        )

    def clear_state(self, key: str) -> None:
        self.conn.execute("DELETE FROM reservation_state WHERE dedupe_key=?", (key,))

    def all_states(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM reservation_state ORDER BY updated_at DESC").fetchall()

    # -- runs ---------------------------------------------------------------
    def start_run(self, job_id: str, mode: str, target_at: str, log_path: str,
                  launch_drift_ms: float | None, clock_offset_ms: float | None) -> int:
        cur = self.conn.execute(
            "INSERT INTO runs(job_id, mode, started_at, target_at, log_path, launch_drift_ms, clock_offset_ms)"
            " VALUES(?,?,?,?,?,?,?)",
            (job_id, mode, _now(), target_at, log_path, launch_drift_ms, clock_offset_ms),
        )
        return int(cur.lastrowid)

    def finish_run(self, run_id: int, result: str, detail: str, submit_drift_ms: float | None,
                   screenshots: list[str]) -> None:
        self.conn.execute(
            "UPDATE runs SET finished_at=?, result=?, detail=?, submit_drift_ms=?, screenshots=? WHERE id=?",
            (_now(), result, detail, submit_drift_ms, json.dumps(screenshots, ensure_ascii=False), run_id),
        )

    def runs(self, job_id: str | None = None, limit: int = 100) -> list[sqlite3.Row]:
        if job_id:
            return self.conn.execute("SELECT * FROM runs WHERE job_id=? ORDER BY id DESC LIMIT ?",
                                     (job_id, limit)).fetchall()
        return self.conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()

    def run(self, run_id: int) -> Optional[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()


class AlreadyRunning(Exception):
    pass


@contextlib.contextmanager
def exclusive_lock(name: str) -> Iterator[None]:
    """Process-level lock so two runs can never drive the phone at once."""
    paths.ensure_dirs()
    lock_path = paths.DATA_DIR / f".{name}.lock"
    fh = open(lock_path, "w")
    try:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as e:
            raise AlreadyRunning(f"another run holds {lock_path}") from e
        yield
    finally:
        with contextlib.suppress(Exception):
            fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()
