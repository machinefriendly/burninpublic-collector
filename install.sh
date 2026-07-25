#!/bin/bash
# Install TakToken collector: copies scripts to ~/.aiwork/bin (outside macOS
# TCC-protected folders so launchd may run them) and loads the two agents.
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
BIN="$HOME/.aiwork/bin"
AGENTS="$HOME/Library/LaunchAgents"

mkdir -p "$BIN" "$AGENTS"
cp "$SRC"/collect_place.sh "$SRC"/parse_usage.py "$SRC"/join_and_push.py \
   "$SRC"/places.py "$SRC"/local_schema.sql "$BIN/"
chmod +x "$BIN/collect_place.sh"

for name in collect sync; do
    plist="$AGENTS/com.sig.aiwork.$name.plist"
    sed "s|REPLACE_ME|$BIN|g" "$SRC/launchd/com.sig.aiwork.$name.plist" > "$plist"
    launchctl unload "$plist" 2>/dev/null || true
    launchctl load "$plist"
done

echo "installed to $BIN — agents loaded:"
launchctl list | grep aiwork || true
echo "reports still written to $SRC/reports/ when run manually with AIWORK_LOCAL_ONLY=1"
