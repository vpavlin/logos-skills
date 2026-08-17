#!/usr/bin/env bash
# update.sh — bump a Loam app's loam-transport submodule and rebuild the release APK,
# handling the build gotchas (see SKILL.md). Does NOT commit/push/publish — that's the
# caller's job (so it can wire UI, bump version, and merge first).
#
#   update.sh <app-dir> [ref]        # ref defaults to origin/main
#
# Prints the built APK path + its package/version on success.
set -euo pipefail

APP="${1:?usage: update.sh <app-dir> [ref]}"
REF="${2:-origin/main}"
ANDROID_HOME="${ANDROID_HOME:-$HOME/Android/Sdk}"; export ANDROID_HOME
APP="$(cd "$APP" && pwd)"

# locate the submodule (path may be logos-transport-pkg or loam-transport-pkg)
SUB=""
for cand in mobile/src/lib/loam-transport-pkg mobile/src/lib/logos-transport-pkg; do
  [ -e "$APP/$cand" ] && SUB="$cand" && break
done
[ -n "$SUB" ] || { echo "!! no loam-transport submodule under $APP/mobile/src/lib" >&2; exit 1; }
echo "== app: $APP"
echo "== submodule: $SUB  ->  $REF"

# 1. bump the submodule pin
( cd "$APP/$SUB" && git fetch -q origin && git checkout -q "$REF" && echo "   now @ $(git rev-parse --short HEAD)" )
( cd "$APP" && git add "$SUB" )

# 2. rebuild — prebuild --clean WIPES local.properties, so restore it before gradle
cd "$APP/mobile"
echo "== expo prebuild (android, --clean)"
npx expo prebuild --platform android --clean >/dev/null 2>&1 || { echo "!! prebuild failed" >&2; exit 1; }
echo "sdk.dir=${ANDROID_HOME:-$HOME/Android/Sdk}" > android/local.properties      # <-- the gotcha
echo "== gradle assembleRelease"
( cd android && ./gradlew assembleRelease --console=plain --no-daemon --max-workers=2 -Dorg.gradle.jvmargs="-Xmx2g" )

APK="$APP/mobile/android/app/build/outputs/apk/release/app-release.apk"
[ -f "$APK" ] || { echo "!! no APK produced" >&2; exit 1; }
AAPT="$(ls "$ANDROID_HOME"/build-tools/*/aapt2 2>/dev/null | tail -1)"
echo "== BUILT: $APK"
[ -n "$AAPT" ] && "$AAPT" dump badging "$APK" 2>/dev/null | grep -oE "package: name='[^']+'|versionName='[^']+'" | sed 's/^/   /'
