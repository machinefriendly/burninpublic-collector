#!/bin/bash
# Install TakToken collector: copies scripts to ~/.aiwork/bin (outside macOS
# TCC-protected folders so launchd may run them) and loads the two agents.
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
BIN="$HOME/.aiwork/bin"
AGENTS="$HOME/Library/LaunchAgents"

mkdir -p "$BIN" "$AGENTS"
cp "$SRC"/collect_place.sh "$SRC"/parse_usage.py "$SRC"/join_and_push.py \
   "$SRC"/places.py "$SRC"/locate_places.py "$SRC"/local_schema.sql "$BIN/"
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

echo "installed to $BIN — agents loaded:"
launchctl list | grep aiwork || true
echo "reports still written to $SRC/reports/ when run manually with AIWORK_LOCAL_ONLY=1"
