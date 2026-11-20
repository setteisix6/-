#!/usr/bin/env bash
# Diagnose WebDriverAgent code-signing problems (read-only).
#   bash scripts/check_signing.sh
set -u
h(){ printf "\n== %s\n" "$*"; }

h "1. Apple Development certificates: Team ID is the OU field (NOT the (XXXXXXXXXX) in the name)"
security find-certificate -a -c "Apple Development" -p 2>/dev/null > /tmp/conpass_certs.pem
if [ -s /tmp/conpass_certs.pem ]; then
  awk '/BEGIN CERT/{n++} {print > ("/tmp/conpass_cert_" n ".pem")}' /tmp/conpass_certs.pem
  for f in /tmp/conpass_cert_*.pem; do
    openssl x509 -in "$f" -noout -subject -enddate -nameopt multiline 2>/dev/null \
      | grep -E "commonName|organizationalUnitName|organizationName|notAfter" | sed 's/^ */  /'
    echo "  --"
  done
  rm -f /tmp/conpass_cert_*.pem
else
  echo "  no 'Apple Development' certificate in keychain"
fi
rm -f /tmp/conpass_certs.pem

h "2. Signing identities (invalid ones show the reason, e.g. CSSMERR_TP_NOT_TRUSTED)"
security find-identity -p codesigning 2>&1 | sed 's/^/  /'

h "3. Apple intermediate certificates (WWDR G3 is needed for Apple Development certs)"
for cn in "Apple Worldwide Developer Relations Certification Authority" "Apple Root CA"; do
  n=$(security find-certificate -a -c "$cn" 2>/dev/null | grep -c '"labl"')
  echo "  $cn: $n found"
done

h "4. Teams Xcode knows about (accounts signed in to Xcode)"
for k in IDEProvisioningTeamByIdentifier IDEProvisioningTeams; do
  v=$(defaults read com.apple.dt.Xcode "$k" 2>/dev/null) && { echo "  [$k]"; echo "$v" | grep -E "teamID|teamName|teamType|isFreeProvisioningTeam" | sed 's/^ */    /'; }
done

h "5. Provisioning profiles on this Mac"
for d in "$HOME/Library/Developer/Xcode/UserData/Provisioning Profiles" "$HOME/Library/MobileDevice/Provisioning Profiles"; do
  [ -d "$d" ] || continue
  for p in "$d"/*.mobileprovision; do
    [ -e "$p" ] || continue
    security cms -D -i "$p" 2>/dev/null > /tmp/conpass_prof.plist || continue
    name=$(/usr/libexec/PlistBuddy -c "Print :Name" /tmp/conpass_prof.plist 2>/dev/null)
    team=$(/usr/libexec/PlistBuddy -c "Print :TeamIdentifier:0" /tmp/conpass_prof.plist 2>/dev/null)
    appid=$(/usr/libexec/PlistBuddy -c "Print :Entitlements:application-identifier" /tmp/conpass_prof.plist 2>/dev/null)
    exp=$(/usr/libexec/PlistBuddy -c "Print :ExpirationDate" /tmp/conpass_prof.plist 2>/dev/null)
    echo "  $name | team=$team | $appid | expires $exp"
  done
done
rm -f /tmp/conpass_prof.plist

h "6. device.yaml"
grep -E "xcode_org_id|updated_wda_bundle_id|allow_provisioning" "$(dirname "$0")/../config/device.yaml" 2>/dev/null | sed 's/^/  /'
