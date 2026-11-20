#!/usr/bin/env bash
# List installed user apps and highlight likely 777CON-PASS candidates. Confirm on device before use.
# Supports both ideviceinstaller syntaxes (new: `list --user`, old: `-l`) and falls back to devicectl.
U=${1:-$(idevice_id -l 2>/dev/null | head -1)}
[ -n "$U" ] || { echo "no device found (idevice_id -l). Pass UDID as argument."; exit 1; }
PAT="con|pass|777|sammy"
OUT=""
if command -v ideviceinstaller >/dev/null; then
  OUT=$(ideviceinstaller -u "$U" list --user 2>/dev/null) || OUT=""
  [ -n "$OUT" ] || OUT=$(ideviceinstaller -u "$U" -l 2>/dev/null) || OUT=""
fi
if [ -z "$OUT" ]; then
  OUT=$(xcrun devicectl device info apps --device "$U" 2>/dev/null) || { echo "could not list apps"; exit 1; }
fi
echo "$OUT" | grep -iE "$PAT" || { echo "(no match for /$PAT/; full list below)"; echo "$OUT"; }
