"""Simulated iPhone app: screens are sets of element ids; taps move between screens."""
from __future__ import annotations

from pathlib import Path


class FakeApp:
    def __init__(self, screens: dict[str, set[str]], transitions: dict[tuple[str, str], str], start="home"):
        self.screens = screens
        self.transitions = transitions
        self.current = start
        self.start = start
        self.taps: list[str] = []
        self.typed: list[str] = []
        self.on_tap = None          # hook(value) -> optional override screen
        self.running = True

    def elements(self):
        return self.screens.get(self.current, set()) if self.running else set()


class FakeDevice:
    def __init__(self, app: FakeApp):
        self.app = app
        self.shots: list[Path] = []
        self.quit_called = False

    def exists(self, loc):
        return loc.value in self.app.elements()

    def wait_for(self, loc, timeout):
        return self.exists(loc)

    def tap(self, loc, timeout):
        if not self.exists(loc):
            raise LookupError(f"element not found: {loc.value}")
        self.app.taps.append(loc.value)
        nxt = self.app.on_tap(loc.value) if self.app.on_tap else None
        if nxt is None:
            nxt = self.app.transitions.get((self.app.current, loc.value))
        if nxt:
            self.app.current = nxt

    def type_text(self, loc, text, timeout, clear=False):
        if not self.exists(loc):
            raise LookupError(loc.value)
        self.app.typed.append(text)

    def scroll_to_tap(self, loc, timeout):
        self.tap(loc, timeout)

    def back(self):
        pass

    def screenshot(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x89PNG fake")
        self.shots.append(path)
        return path

    def page_source(self):
        return "<XCUIElementTypeApplication/>"

    def activate_app(self):
        self.app.running = True

    def terminate_app(self):
        self.app.running = False
        self.app.current = self.app.start

    def app_state(self):
        return 4 if self.app.running else 1

    def quit(self):
        self.quit_called = True
