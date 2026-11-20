#!/usr/bin/env bash
# Build WebDriverAgent for the iPhone with exactly the arguments Appium uses,
# and show the real xcodebuild errors (Appium only reports "code 65").
#   bash scripts/build_wda.sh            # build-for-testing only
#   bash scripts/build_wda.sh --full     # also show the full log path
set -u
cd "$(dirname "$0")/.."
val(){ sed -nE "s/^$1:[[:space:]]*\"?([^\"#]*)\"?.*/\1/p" config/device.yaml | head -1 | xargs; }
UDID=$(val udid); TEAM=$(val xcode_org_id); SIGN=$(val xcode_signing_id); BID=$(val updated_wda_bundle_id)
SIGN=${SIGN:-Apple Development}
WDA=$(ls -d "$HOME"/.appium/node_modules/appium-xcuitest-driver/node_modules/appium-webdriveragent 2>/dev/null | head -1)
[ -d "$WDA" ] || { echo "WebDriverAgent not found under ~/.appium"; exit 1; }
echo "WDA: $WDA ($(sed -nE 's/.*"version": "([^"]+)".*/\1/p' "$WDA/package.json" | head -1))"
echo "UDID=$UDID TEAM=$TEAM SIGN=$SIGN BUNDLE=$BID"
LOG=data/logs/wda-build-$(date +%Y%m%d-%H%M%S).log; mkdir -p data/logs
set -o pipefail
xcodebuild build-for-testing \
  -project "$WDA/WebDriverAgent.xcodeproj" -scheme WebDriverAgentRunner \
  -destination "id=$UDID" \
  -allowProvisioningUpdates -allowProvisioningDeviceRegistration \
  DEVELOPMENT_TEAM="$TEAM" CODE_SIGN_IDENTITY="$SIGN" CODE_SIGN_STYLE=Automatic \
  ${BID:+PRODUCT_BUNDLE_IDENTIFIER="$BID"} \
  GCC_TREAT_WARNINGS_AS_ERRORS=0 COMPILER_INDEX_STORE_ENABLE=NO > "$LOG" 2>&1
RC=$?
echo "---- relevant lines (full log: $LOG) ----"
grep -nE "error:|error -|Signing|Provisioning|profile|certificate|Team|xctrunner|BUILD (SUCCEEDED|FAILED)|\*\* " "$LOG" | grep -vE "^\s*$" | tail -40
echo "exit code: $RC"
exit $RC
