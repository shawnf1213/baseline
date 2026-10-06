#!/usr/bin/env bash
# Build and submit the iOS app with EAS, authenticating to Apple with the App
# Store Connect API key (no Apple ID password, no 2FA prompt).
#
#   scripts/eas-ios.sh build            # production build, returns at once
#   scripts/eas-ios.sh status           # list recent builds
#   scripts/eas-ios.sh submit           # submit the latest finished build to TestFlight
#   scripts/eas-ios.sh build-and-submit # build, wait, then submit
#
# The .p8 key lives in mobile/.secrets/ (git-ignored). The key id, issuer id and
# team id are identifiers, not secrets; they also sit in eas.json's submit
# profile. Run from the mobile/ directory.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

KEY=".secrets/AuthKey_8F66NL8552.p8"
[ -f "$KEY" ] || { echo "missing $KEY — download it from App Store Connect → Users and Access → Integrations → App Store Connect API, and put it there"; exit 2; }

export EXPO_ASC_API_KEY_PATH="$PWD/$KEY"
export EXPO_ASC_KEY_ID="8F66NL8552"
export EXPO_ASC_ISSUER_ID="b81a68cc-d55f-48d8-b96c-64a793bdc525"
export EXPO_APPLE_TEAM_ID="WHD8WZRR58"
export EXPO_APPLE_TEAM_TYPE="${EXPO_APPLE_TEAM_TYPE:-INDIVIDUAL}"   # or COMPANY_OR_ORGANIZATION

case "${1:-build}" in
  build)
    npx --yes eas-cli@latest build -p ios -e production --non-interactive --no-wait ;;
  build-and-submit)
    npx --yes eas-cli@latest build -p ios -e production --non-interactive --auto-submit-with-profile production ;;
  status)
    npx --yes eas-cli@latest build:list -p ios --limit 5 --non-interactive ;;
  submit)
    npx --yes eas-cli@latest submit -p ios -e production --latest --non-interactive ;;
  *)
    echo "usage: $0 build|build-and-submit|status|submit"; exit 2 ;;
esac
