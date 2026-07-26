#!/bin/bash
# Install the BurnInPublic collector.
#
#   One-liner (no clone needed):
#     curl -fsSL https://raw.githubusercontent.com/machinefriendly/burninpublic-collector/main/install.sh | bash
#
#   Or from a clone:  ./install.sh
#
# Copies scripts to ~/.aiwork/bin (outside macOS TCC-protected folders so
# launchd may run them) and loads the two background agents.
set -euo pipefail

REPO="https://github.com/machinefriendly/burninpublic-collector"
SRC="$(cd "$(dirname "$0")" && pwd)"
BIN="$HOME/.aiwork/bin"
AGENTS="$HOME/Library/LaunchAgents"

# Running via `curl | bash`? Fetch the source tarball first.
if [ ! -f "$SRC/collect_place.sh" ]; then
    TMP="$(mktemp -d)"
    trap 'rm -rf "$TMP"' EXIT
    echo "downloading $REPO ..."
    curl -fsSL "$REPO/archive/refs/heads/main.tar.gz" | tar -xz -C "$TMP"
    SRC="$TMP/burninpublic-collector-main"
fi

mkdir -p "$BIN" "$AGENTS"
cp "$SRC"/collect_place.sh "$SRC"/parse_usage.py "$SRC"/join_and_push.py \
   "$SRC"/places.py "$SRC"/locate_places.py "$SRC"/place_key.py \
   "$SRC"/login.py "$SRC"/local_schema.sql "$BIN/"
chmod +x "$BIN/collect_place.sh"

# Location helper as an .app bundle — the only form macOS reliably shows a
# location permission prompt for (bare CLI binaries are silently denied).
if command -v swiftc >/dev/null 2>&1; then
    APP="$BIN/AiworkLocate.app"
    rm -rf "$APP"
    mkdir -p "$APP/Contents/MacOS"
    cp "$SRC/helpers/Info.plist" "$APP/Contents/Info.plist"
    swiftc "$SRC/helpers/aiwork-locate.swift" \
        -o "$APP/Contents/MacOS/aiwork-locate" -framework CoreLocation \
    && codesign -s - -f "$APP" \
    && echo "built AiworkLocate.app (open it once to grant the location prompt)"
else
    echo "swiftc not found — geolocation helper skipped (usage tracking unaffected)"
fi

for name in collect sync; do
    plist="$AGENTS/com.sig.aiwork.$name.plist"
    sed "s|REPLACE_ME|$BIN|g" "$SRC/launchd/com.sig.aiwork.$name.plist" > "$plist"
    launchctl unload "$plist" 2>/dev/null || true
    launchctl load "$plist"
done

echo
echo "installed to $BIN — agents loaded:"
launchctl list | grep aiwork || true
echo
echo "next steps:"
echo "  1. create an account at https://burninpublic.com"
echo "  2. python3 $BIN/login.py            # connect this machine"
echo "  3. python3 $BIN/places.py           # name your places when they appear"
echo "preview locally without uploading:"
echo "  AIWORK_LOCAL_ONLY=1 python3 $BIN/join_and_push.py"
