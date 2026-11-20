"""Device abstraction: real Appium/XCUITest session, plus helpers.

The engine only talks to the `Device` protocol so it can be tested against
`tests/fake_device.py` without an iPhone.
"""
from __future__ import annotations

import base64
import logging
import time
from pathlib import Path
from typing import Optional, Protocol

from .selectors import Locator

log = logging.getLogger("conpass.device")


class DeviceError(Exception):
    """Transport/session problem (WDA died, USB unplugged, Appium down)."""


class Device(Protocol):
    def exists(self, loc: Locator) -> bool: ...
    def wait_for(self, loc: Locator, timeout: float) -> bool: ...
    def tap(self, loc: Locator, timeout: float) -> None: ...
    def type_text(self, loc: Locator, text: str, timeout: float, clear: bool = False) -> None: ...
    def scroll_to_tap(self, loc: Locator, timeout: float) -> None: ...
    def back(self) -> None: ...
    def screenshot(self, path: Path) -> Path: ...
    def page_source(self) -> str: ...
    def activate_app(self) -> None: ...
    def terminate_app(self) -> None: ...
    def app_state(self) -> int: ...
    def quit(self) -> None: ...


def build_options(cfg):
    """XCUITest capabilities for a real device. Separate for unit testing."""
    from appium.options.ios import XCUITestOptions

    if not cfg.udid or not cfg.bundle_id:
        raise DeviceError("config/device.yaml: udid and bundle_id are required")
    opts = XCUITestOptions()
    opts.platform_name = "iOS"
    opts.automation_name = "XCUITest"
    opts.udid = cfg.udid
    opts.bundle_id = cfg.bundle_id
    opts.no_reset = True            # keep the user's login session
    opts.full_reset = False
    opts.new_command_timeout = cfg.new_command_timeout
    opts.set_capability("appium:wdaLaunchTimeout", cfg.wda_launch_timeout_ms)
    opts.set_capability("appium:showXcodeLog", cfg.show_xcode_log)
    opts.set_capability("appium:autoAcceptAlerts", False)  # never auto-confirm dialogs
    if cfg.xcode_org_id:
        # The driver passes these as DEVELOPMENT_TEAM / CODE_SIGN_IDENTITY to xcodebuild
        opts.set_capability("appium:xcodeOrgId", cfg.xcode_org_id)
        opts.set_capability("appium:xcodeSigningId", cfg.xcode_signing_id)
    if cfg.updated_wda_bundle_id:
        # -> PRODUCT_BUNDLE_IDENTIFIER; the runner gets ".xctrunner" appended
        opts.set_capability("appium:updatedWDABundleId", cfg.updated_wda_bundle_id)
    if cfg.allow_provisioning_device_registration:
        # Driver adds `-allowProvisioningUpdates -allowProvisioningDeviceRegistration`,
        # letting Xcode create the (free) provisioning profile automatically.
        opts.set_capability("appium:allowProvisioningDeviceRegistration", True)
    if cfg.xcode_config_file:
        opts.set_capability("appium:xcodeConfigFile", cfg.xcode_config_file)
    if cfg.derived_data_path:
        opts.set_capability("appium:derivedDataPath", cfg.derived_data_path)
    return opts


class AppiumDevice:
    """Thin wrapper over Appium-Python-Client with XCUITest options."""

    def __init__(self, cfg):
        from appium import webdriver

        self.cfg = cfg
        opts = build_options(cfg)
        try:
            self.driver = webdriver.Remote(cfg.appium_url, options=opts)
        except Exception as e:  # noqa: BLE001
            raise DeviceError(f"could not start Appium session: {e}") from e
        self.driver.implicitly_wait(cfg.implicit_wait_s)

    # -- locating -----------------------------------------------------------
    def _by(self, loc: Locator):
        from appium.webdriver.common.appiumby import AppiumBy
        return {
            "accessibility_id": AppiumBy.ACCESSIBILITY_ID,
            "predicate": AppiumBy.IOS_PREDICATE,
            "class_chain": AppiumBy.IOS_CLASS_CHAIN,
            "xpath": AppiumBy.XPATH,
            "name": AppiumBy.NAME,
        }[loc.by]

    def _find(self, loc: Locator):
        if loc.by in ("coords",):
            return None
        if loc.by == "image":
            from appium.webdriver.common.appiumby import AppiumBy
            self.driver.update_settings({"imageMatchThreshold": loc.threshold})
            b64 = base64.b64encode(Path(loc.value).read_bytes()).decode()
            els = self.driver.find_elements(AppiumBy.IMAGE, b64)
        else:
            els = self.driver.find_elements(self._by(loc), loc.value)
        for el in els:
            try:
                if el.is_displayed():
                    return el
            except Exception:  # noqa: BLE001 - stale element
                continue
        return None

    def exists(self, loc: Locator) -> bool:
        if loc.by == "coords":
            return True
        try:
            return self._find(loc) is not None
        except Exception as e:  # noqa: BLE001
            if _is_session_error(e):
                raise DeviceError(str(e)) from e
            return False

    def wait_for(self, loc: Locator, timeout: float) -> bool:
        end = time.monotonic() + timeout
        while True:
            if self.exists(loc):
                return True
            if time.monotonic() >= end:
                return False
            time.sleep(0.25)

    def tap(self, loc: Locator, timeout: float) -> None:
        if loc.by == "coords":
            x, y = (float(v) for v in loc.value.split(","))
            size = self.driver.get_window_size()
            self.driver.execute_script("mobile: tap", {"x": x * size["width"], "y": y * size["height"]})
            return
        end = time.monotonic() + timeout
        while True:
            el = self._find(loc)
            if el is not None:
                el.click()
                return
            if time.monotonic() >= end:
                raise LookupError(f"element not found: {loc.by}={loc.value}")
            time.sleep(0.25)

    def type_text(self, loc: Locator, text: str, timeout: float, clear: bool = False) -> None:
        if not self.wait_for(loc, timeout):
            raise LookupError(f"input not found: {loc.by}={loc.value}")
        el = self._find(loc)
        if clear:
            el.clear()
        el.send_keys(text)

    def scroll_to_tap(self, loc: Locator, timeout: float) -> None:
        end = time.monotonic() + timeout
        swipes = 0
        while time.monotonic() < end and swipes < 15:
            if self.exists(loc):
                return self.tap(loc, 2)
            self.driver.execute_script("mobile: swipe", {"direction": "up"})
            swipes += 1
            time.sleep(0.4)
        raise LookupError(f"element not found after scrolling: {loc.by}={loc.value}")

    def back(self) -> None:
        self.driver.back()

    # -- app/session --------------------------------------------------------
    def screenshot(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.driver.get_screenshot_as_file(str(path))
        return path

    def page_source(self) -> str:
        return self.driver.page_source

    def activate_app(self) -> None:
        self.driver.activate_app(self.cfg.bundle_id)

    def terminate_app(self) -> None:
        self.driver.terminate_app(self.cfg.bundle_id)

    def app_state(self) -> int:
        # 0 not installed, 1 not running, 2/3 background, 4 foreground
        return int(self.driver.query_app_state(self.cfg.bundle_id))

    def quit(self) -> None:
        try:
            self.driver.quit()
        except Exception:  # noqa: BLE001
            pass


def _is_session_error(e: Exception) -> bool:
    msg = str(e).lower()
    return any(s in msg for s in ("invalid session", "session is either terminated",
                                   "connection refused", "socket hang up", "could not proxy"))


def connect(cfg=None) -> AppiumDevice:
    from .config import load_device
    return AppiumDevice(cfg or load_device())
