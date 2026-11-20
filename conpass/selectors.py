"""Declarative screen / step definitions loaded from config/selectors.yaml.

Nothing in this file knows what the 777CON-PASS UI looks like. All app-specific
knowledge lives in the YAML and must be filled in from real-device recon
(`python -m conpass.cli recon`). Every locator carries `verified: true|false`;
production runs refuse to start while any locator they need is unverified.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, Field

from . import paths

By = Literal["accessibility_id", "predicate", "class_chain", "xpath", "name", "image", "coords"]

# Screens the engine itself cares about. Others may be defined freely.
INTERRUPT_SCREENS = ("login", "captcha", "network_error", "maintenance", "force_update", "system_alert")
RESULT_SCREENS = ("complete", "already_applied", "closed", "submit_error")


class Locator(BaseModel):
    by: By
    value: str = ""                 # selector, image path, or "x,y" (0..1 relative) for coords
    verified: bool = False
    note: str = ""
    threshold: float = 0.8          # image match threshold

    def render(self, ctx: dict) -> "Locator":
        return self.model_copy(update={"value": render(self.value, ctx)})

    @property
    def is_placeholder(self) -> bool:
        return (not self.value) or "TODO" in self.value


class Screen(BaseModel):
    markers: list[Locator] = []     # ALL must be present to identify the screen
    description: str = ""


class Step(BaseModel):
    name: str
    action: Literal["tap", "type", "clear_and_type", "wait", "scroll_to_tap", "back", "pause"] = "tap"
    target: Optional[Locator] = None
    text: str = ""
    expect: Optional[str] = None            # screen name expected after the step
    expect_any: list[str] = []
    timeout_s: Optional[float] = None
    submit: bool = False                    # the single irreversible step
    optional: bool = False                  # skip silently if target absent
    seconds: float = 0.0                    # for action: pause


class VerifyList(BaseModel):
    """How to open the reservation list and decide if our reservation exists."""
    steps: list[Step] = []
    screen: str = "reservation_list"
    match: Optional[Locator] = None         # present => reservation exists


class FlowSpec(BaseModel):
    meta: dict = Field(default_factory=dict)
    screens: dict[str, Screen] = {}
    steps: list[Step] = []
    verify_list: VerifyList = VerifyList()
    home_screen: str = "home"

    # ------------------------------------------------------------------
    def submit_index(self) -> int:
        idx = [i for i, s in enumerate(self.steps) if s.submit]
        if len(idx) != 1:
            raise ValueError(f"exactly one step must have submit: true (found {len(idx)})")
        return idx[0]

    def all_locators(self, include_verify: bool = True):
        for name, sc in self.screens.items():
            for i, m in enumerate(sc.markers):
                yield f"screens.{name}.markers[{i}]", m
        for s in self.steps:
            if s.target:
                yield f"steps.{s.name}.target", s.target
        if include_verify:
            for s in self.verify_list.steps:
                if s.target:
                    yield f"verify_list.steps.{s.name}.target", s.target
            if self.verify_list.match:
                yield "verify_list.match", self.verify_list.match

    def unverified(self) -> list[str]:
        """Locators that production depends on and are not verified."""
        needed_screens = {self.home_screen, self.verify_list.screen, *RESULT_SCREENS[:2]}
        for s in self.steps:
            if s.expect:
                needed_screens.add(s.expect)
            needed_screens.update(s.expect_any)
        out = []
        for key, loc in self.all_locators():
            if key.startswith("screens."):
                scr = key.split(".")[1]
                if scr not in needed_screens and scr not in INTERRUPT_SCREENS:
                    continue
            if not loc.verified or loc.is_placeholder:
                out.append(key)
        for scr in needed_screens:
            if scr not in self.screens or not self.screens[scr].markers:
                out.append(f"screens.{scr} (missing)")
        return out


def render(template: str, ctx: dict) -> str:
    try:
        return template.format(**ctx)
    except (KeyError, IndexError, ValueError):
        return template


def predicate_quote(s: str) -> str:
    return s.replace("\\", "\\\\").replace("'", "\\'")


def make_context(store: str, visit_date: date) -> dict:
    """Template variables available in selectors.yaml."""
    return {
        "store": store,
        "store_q": predicate_quote(store),
        "visit_date": visit_date,
        "month": visit_date.month,
        "day": visit_date.day,
        "year": visit_date.year,
        "weekday_ja": "月火水木金土日"[visit_date.weekday()],
    }


def load_flow(path: Path | None = None) -> FlowSpec:
    path = path or paths.SELECTORS_FILE
    return FlowSpec.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})
