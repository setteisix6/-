from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(os.environ.get("CONPASS_ROOT", Path(__file__).resolve().parent.parent))
CONFIG_DIR = ROOT / "config"
DATA_DIR = Path(os.environ.get("CONPASS_DATA", ROOT / "data"))
LOG_DIR = DATA_DIR / "logs"
SHOT_DIR = DATA_DIR / "screenshots"
RECON_DIR = DATA_DIR / "recon"
DB_PATH = DATA_DIR / "conpass.sqlite3"
RESERVATIONS_FILE = CONFIG_DIR / "reservations.yaml"
SELECTORS_FILE = CONFIG_DIR / "selectors.yaml"
DEVICE_FILE = CONFIG_DIR / "device.yaml"


def ensure_dirs() -> None:
    for d in (DATA_DIR, LOG_DIR, SHOT_DIR, RECON_DIR):
        d.mkdir(parents=True, exist_ok=True)
