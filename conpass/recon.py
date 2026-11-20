"""Screen reconnaissance: capture what the real app shows, so selectors are
built from facts instead of guesses.

Usage (on the Mac, Appium server running, iPhone unlocked):
    python -m conpass.cli recon            # interactive: navigate by hand, press Enter to capture
    python -m conpass.cli probe            # test every locator in selectors.yaml on the current screen
"""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

from . import paths
from .selectors import FlowSpec, make_context

INTERESTING = {"XCUIElementTypeButton", "XCUIElementTypeStaticText", "XCUIElementTypeTextField",
               "XCUIElementTypeSearchField", "XCUIElementTypeCell", "XCUIElementTypeLink",
               "XCUIElementTypeOther", "XCUIElementTypeImage", "XCUIElementTypeTabBar",
               "XCUIElementTypeNavigationBar", "XCUIElementTypeWebView", "XCUIElementTypeSwitch",
               "XCUIElementTypePickerWheel", "XCUIElementTypeSecureTextField"}


def summarize_source(xml: str, limit: int = 400) -> list[dict]:
    """Flatten the XCUITest page source into a list of candidate elements."""
    out: list[dict] = []
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return out
    for el in root.iter():
        t = el.tag
        if t not in INTERESTING:
            continue
        a = el.attrib
        name, label, value = a.get("name"), a.get("label"), a.get("value")
        if t == "XCUIElementTypeOther" and not (name or label):
            continue
        if a.get("visible") == "false":
            continue
        out.append({"type": t.replace("XCUIElementType", ""), "name": name, "label": label,
                    "value": value, "enabled": a.get("enabled"),
                    "rect": [a.get("x"), a.get("y"), a.get("width"), a.get("height")],
                    "accessible": a.get("accessible")})
        if len(out) >= limit:
            break
    return out


def capture(device, label: str, out_dir: Path | None = None) -> Path:
    out_dir = out_dir or (paths.RECON_DIR / datetime.now().strftime("%Y%m%d"))
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{datetime.now().strftime('%H%M%S')}-{label}"
    device.screenshot(out_dir / f"{stem}.png")
    xml = device.page_source()
    (out_dir / f"{stem}.xml").write_text(xml, encoding="utf-8")
    elems = summarize_source(xml)
    (out_dir / f"{stem}.json").write_text(json.dumps(elems, ensure_ascii=False, indent=1), encoding="utf-8")
    webviews = sum(1 for e in elems if e["type"] == "WebView")
    print(f"\n== {stem}: {len(elems)} elements{' (contains WebView)' if webviews else ''}")
    for e in elems[:80]:
        print(f"  {e['type']:<14} name={e['name']!r:<30.30} label={e['label']!r:<30.30} rect={e['rect']}")
    return out_dir / stem


def interactive(device) -> None:
    print("iPhoneで画面を手動で移動し、各画面で Enter を押すとキャプチャします。"
          "ラベル名を入力すると保存名になります（例: home, store_search）。q で終了。")
    while True:
        lbl = input("label> ").strip()
        if lbl.lower() in ("q", "quit", "exit"):
            break
        capture(device, lbl or "screen")


def probe(device, flow: FlowSpec, store: str, visit_date) -> dict:
    """Report which configured screens/locators are present on the current screen."""
    ctx = make_context(store, visit_date)
    report = {"screens": {}, "locators": {}}
    for name, sc in flow.screens.items():
        if sc.markers:
            report["screens"][name] = all(device.exists(m.render(ctx)) for m in sc.markers)
    for key, loc in flow.all_locators():
        if loc.is_placeholder:
            report["locators"][key] = "placeholder"
            continue
        report["locators"][key] = "present" if device.exists(loc.render(ctx)) else "absent"
    return report
