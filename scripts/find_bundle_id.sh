#!/usr/bin/env bash
# List installed user apps and highlight likely 777CON-PASS candidates. Confirm on device before use.
set -e
U=${1:-$(idevice_id -l | head -1)}
if command -v ideviceinstaller >/dev/null; then ideviceinstaller -u "$U" -l 2>/dev/null | grep -iE "con|pass|777|sammy" || ideviceinstaller -u "$U" -l
else xcrun devicectl device info apps --device "$U" | grep -iE "con|pass|777|sammy"; fi
