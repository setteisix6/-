#!/usr/bin/env bash
# Phase 1 setup (idempotent). Installs only what is missing; versions are resolved
# at run time from npm/Homebrew rather than hard-coded.
# Steps needing admin/Apple ID (Xcode install, license, signing) are printed, not forced.
set -euo pipefail
cd "$(dirname "$0")/.."
say(){ printf "\n\033[1m>> %s\033[0m\n" "$*"; }
has(){ command -v "$1" >/dev/null 2>&1; }
[ "$(uname -s)" = Darwin ] || { echo "run on macOS"; exit 1; }

say "Xcode check"
if ! xcodebuild -version >/dev/null 2>&1; then
  echo "!! Xcode (full app) is required. Install from the App Store, then run:"
  echo "   sudo xcode-select -s /Applications/Xcode.app/Contents/Developer"
  echo "   sudo xcodebuild -license accept && sudo xcodebuild -runFirstLaunch"
  echo "   then re-run this script."; exit 2
fi
xcodebuild -version

say "Homebrew"
has brew || { echo "Install Homebrew first: https://brew.sh (needs admin password)"; exit 2; }
brew update

say "Node.js (must satisfy Appium's engines.node)"
has node || brew install node
REQ=$(npm view appium engines.node)
if ! npx --yes semver -r "$REQ" "$(node -v)" >/dev/null; then
  echo "node $(node -v) does not satisfy '$REQ'; installing Homebrew node (current)"; brew install node || brew upgrade node
  npx --yes semver -r "$REQ" "$(node -v)" >/dev/null || { echo "still incompatible: install an LTS satisfying $REQ (e.g. brew install node@24 && brew link --overwrite node@24)"; exit 3; }
fi
echo "node $(node -v) OK for $REQ"

say "libimobiledevice (device info / udid)"
brew list libimobiledevice >/dev/null 2>&1 || brew install libimobiledevice
brew list ideviceinstaller >/dev/null 2>&1 || brew install ideviceinstaller || echo "(ideviceinstaller optional)"

say "Appium + XCUITest driver"
if ! has appium; then npm install -g appium; fi
echo "appium $(appium -v)"
if appium driver list --installed 2>&1 | grep -q xcuitest; then appium driver update xcuitest || true
else appium driver install xcuitest; fi
appium driver doctor xcuitest || echo "(review doctor output above — required items must be fixed)"

say "Python venv"
PY=$(command -v python3)
"$PY" -c 'import sys; assert sys.version_info>=(3,11), sys.version' || { brew install python; PY=$(brew --prefix)/bin/python3; }
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/pip install -U pip >/dev/null
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q

say "Config files"
[ -f config/device.yaml ] || cp config/device.example.yaml config/device.yaml
[ -f config/reservations.yaml ] || cp config/reservations.example.yaml config/reservations.yaml

cat <<'MSG'

== Manual steps (need you) ==========================================
1. iPhone: USB接続 → 「このコンピュータを信頼」→ 設定 > プライバシーとセキュリティ > デベロッパモード ON（再起動）
2. Xcode > Settings > Accounts で Apple ID を追加（無料アカウント可、署名は7日で失効）
3. WebDriverAgent署名:  appium driver run xcuitest open-wda
     WebDriverAgentRunner ターゲット → Signing & Capabilities → Team を選択、
     Bundle ID を一意に（例 com.<name>.WebDriverAgentRunner）→ config/device.yaml の updated_wda_bundle_id に同じ値
4. config/device.yaml に udid / bundle_id / xcode_org_id を記入（bash scripts/diagnose_mac.sh で確認）
5. iPhone: 設定 > 一般 > VPNとデバイス管理 で開発元を信頼（初回WDAインストール後）
6. bash scripts/start_appium.sh → 別ターミナルで .venv/bin/python -m conpass.cli screenshot
MSG
