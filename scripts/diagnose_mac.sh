#!/usr/bin/env bash
# Phase 1: read-only environment diagnosis for Intel Mac + iPhone. Changes nothing.
# Usage: bash scripts/diagnose_mac.sh | tee data/diagnose.txt
set -u
ok(){ printf "  \033[32mOK\033[0m   %s\n" "$*"; }
ng(){ printf "  \033[31mNG\033[0m   %s\n" "$*"; }
wn(){ printf "  \033[33mWARN\033[0m %s\n" "$*"; }
h(){ printf "\n== %s\n" "$*"; }
has(){ command -v "$1" >/dev/null 2>&1; }

h "macOS / CPU"
if [ "$(uname -s)" != "Darwin" ]; then ng "not macOS ($(uname -s)) — run this on the Mac"; exit 1; fi
MACOS=$(sw_vers -productVersion); echo "  macOS $MACOS ($(sw_vers -buildVersion))"
ARCH=$(uname -m); CPU=$(sysctl -n machdep.cpu.brand_string 2>/dev/null)
echo "  arch=$ARCH cpu=$CPU"; [ "$ARCH" = "x86_64" ] && ok "Intel Mac" || wn "not x86_64 (Apple Silicon?)"
echo "  Rosetta/translated: $(sysctl -n sysctl.proc_translated 2>/dev/null || echo 0)"

h "Xcode"
if xcode-select -p >/dev/null 2>&1; then ok "xcode-select path: $(xcode-select -p)"; else ng "Command Line Tools not installed (xcode-select --install)"; fi
if [ -d /Applications/Xcode.app ] || ls -d /Applications/Xcode*.app >/dev/null 2>&1; then
  if XV=$(xcodebuild -version 2>&1); then ok "$(echo "$XV" | tr '\n' ' ')"
    case "$(xcode-select -p)" in *CommandLineTools*) ng "xcode-select points to CLT; run: sudo xcode-select -s /Applications/Xcode.app/Contents/Developer";; esac
    xcodebuild -checkFirstLaunchStatus >/dev/null 2>&1 || wn "first launch not done: sudo xcodebuild -runFirstLaunch"
    xcrun --sdk iphoneos --show-sdk-version >/dev/null 2>&1 && ok "iOS SDK $(xcrun --sdk iphoneos --show-sdk-version)" || ng "iOS SDK missing"
  else ng "xcodebuild failed: $XV (license? sudo xcodebuild -license accept)"; fi
else ng "Xcode.app not found — install from App Store / developer.apple.com (full Xcode is required for real devices)"; fi

h "Homebrew / Node / Python"
has brew && ok "brew $(brew --version | head -1)" || ng "Homebrew missing (https://brew.sh)"
if has node; then ok "node $(node -v), npm $(npm -v)"; else ng "node missing"; fi
if has npm; then REQ=$(npm view appium engines.node 2>/dev/null); echo "  Appium (latest $(npm view appium version 2>/dev/null)) requires node: $REQ"
  if has node && [ -n "$REQ" ]; then npx --yes semver -r "$REQ" "$(node -v)" >/dev/null 2>&1 && ok "node satisfies" || ng "node $(node -v) does not satisfy $REQ"; fi; fi
has python3 && ok "python3 $(python3 -V 2>&1 | cut -d' ' -f2) at $(command -v python3)" || ng "python3 missing"
python3 -c 'import sys; sys.exit(0 if sys.version_info>=(3,11) else 1)' 2>/dev/null || wn "Python >= 3.11 recommended"

h "Appium"
if has appium; then ok "appium $(appium -v 2>/dev/null)"; appium driver list --installed 2>&1 | sed 's/^/  /'
  echo "  latest xcuitest driver: $(npm view appium-xcuitest-driver version 2>/dev/null) (peer appium $(npm view appium-xcuitest-driver peerDependencies.appium 2>/dev/null))"
else ng "appium not installed"; fi

h "iPhone (USB)"
if has xcrun && xcrun devicectl list devices >/dev/null 2>&1; then xcrun devicectl list devices 2>&1 | sed 's/^/  /'; fi
has xcrun && xcrun xctrace list devices 2>/dev/null | sed -n '/== Devices ==/,/== Simulators ==/p' | sed 's/^/  /'
if has idevice_id; then IDS=$(idevice_id -l); [ -n "$IDS" ] && for u in $IDS; do ok "udid $u iOS $(ideviceinfo -u "$u" -k ProductVersion 2>/dev/null) $(ideviceinfo -u "$u" -k ProductType 2>/dev/null) name=$(ideviceinfo -u "$u" -k DeviceName 2>/dev/null)"
  ideviceinfo -u "$u" -k DeveloperModeStatus >/dev/null 2>&1 || true; done || ng "no device via libimobiledevice (unlock iPhone, tap 'Trust')"
else wn "libimobiledevice not installed (brew install libimobiledevice)"; fi

h "Code signing (WebDriverAgent)"
IDN=$(security find-identity -v -p codesigning 2>/dev/null); echo "$IDN" | sed 's/^/  /'
echo "$IDN" | grep -q "Apple Development" && ok "Apple Development identity present" || wn "no 'Apple Development' cert: Xcode > Settings > Accounts で Apple ID を追加"

h "Power / sleep"
pmset -g | grep -E " sleep|disksleep|displaysleep|womp|powernap|autorestart" | sed 's/^/  /'
pmset -g sched | sed 's/^/  /'
echo "  time sync: $(sntp time.apple.com 2>/dev/null | tail -1)"

h "Done. Compatibility refs:"
echo "  https://appium.github.io/appium-xcuitest-driver/latest/getting-started/system-requirements/"
echo "  https://developer.apple.com/support/xcode/   (Xcode <-> macOS <-> iOS matrix)"
