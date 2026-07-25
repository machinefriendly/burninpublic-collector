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

# Location helper (embedded Info.plist so macOS shows the permission prompt)
if command -v swiftc >/dev/null 2>&1; then
    swiftc "$SRC/helpers/aiwork-locate.swift" -o "$BIN/aiwork-locate" \
        -framework CoreLocation \
        -Xlinker -sectcreate -Xlinker __TEXT -Xlinker __info_plist \
        -Xlinker "$SRC/helpers/Info.plist" \
    && codesign -s - -f "$BIN/aiwork-locate" \
    && echo "built aiwork-locate (run it once to grant the location prompt)"
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
