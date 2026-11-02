#!/bin/bash
# Build and ad-hoc sign MorningBoardTVHelper.app, the bundle that owns the kids-tv
# job's Local Network permission. It builds; it does not install or start anything.
#   scripts/build-kids-tv-helper.sh [OUT_DIR]     (default: <repo>/build)
# The build is reproducible: the same source and toolchain give the same cdhash,
# which the ad-hoc signature's designated requirement is pinned to.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${1:-$REPO/build}"
NAME="MorningBoardTVHelper"
EXE="kids-tv-helper"
BUNDLE_ID="com.davidcasas.morningboard.kidstv-helper"
APP="$OUT/$NAME.app"

for tool in clang codesign plutil; do
  command -v "$tool" >/dev/null || { echo "build-kids-tv-helper: $tool not found" >&2; exit 1; }
done
plutil -lint -s "$REPO/helper/Info.plist"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"
cp "$REPO/helper/Info.plist" "$APP/Contents/Info.plist"
printf 'APPL????' > "$APP/Contents/PkgInfo"

# No -g: debug info would carry build paths and make the binary differ per checkout.
# The Info.plist is also embedded (__TEXT,__info_plist), as TN3179 suggests for an
# executable that may be seen outside its bundle.
clang -Os -Wall -Wextra -Werror -arch arm64 -mmacosx-version-min=15.0 \
  -sectcreate __TEXT __info_plist "$REPO/helper/Info.plist" \
  -o "$APP/Contents/MacOS/$EXE" "$REPO/helper/kids-tv-helper.c"

# Ad-hoc ("-"): no certificate, no team. Hardened runtime on; no entitlements.
# The bundle has one executable and no nested code, so --deep is not needed.
codesign --force --sign - --identifier "$BUNDLE_ID" --options runtime "$APP"
codesign --verify --strict --verbose=1 "$APP"

cdhash="$(codesign -dvvv "$APP" 2>&1 | sed -n 's/^CDHash=//p')"
echo "built $APP"
echo "identifier $BUNDLE_ID"
echo "cdhash $cdhash"
