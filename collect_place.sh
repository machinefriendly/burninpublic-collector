#!/bin/bash
# Location sampler. Default-gateway MAC as the place fingerprint. Runs every 5 min.
set -euo pipefail

DB="${AIWORK_DB:-$HOME/.aiwork/local.db}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

mkdir -p "$(dirname "$DB")"
sqlite3 "$DB" < "$SCRIPT_DIR/local_schema.sql"

GW_IP=$(route -n get default 2>/dev/null | awk '/gateway/{print $2}')
if [ -z "${GW_IP:-}" ]; then
    echo "no default gateway (offline?) — nothing sampled" >&2
    exit 0
fi

RAW_MAC=$(arp -n "$GW_IP" 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="at"){print $(i+1); exit}}')
if [ -z "${RAW_MAC:-}" ] || [ "$RAW_MAC" = "(incomplete)" ]; then
    echo "gateway MAC not resolvable — nothing sampled" >&2
    exit 0
fi

# macOS arp drops leading zeros (a:b:c:...) — normalize to aa:bb:cc:dd:ee:ff
MAC=$(echo "$RAW_MAC" | awk -F: '{for(i=1;i<=NF;i++) printf "%s%02x", (i>1?":":""), strtonum("0x"$i)}' 2>/dev/null || true)
if [ -z "$MAC" ]; then
    MAC=$(echo "$RAW_MAC" | tr 'A-F' 'a-f' | awk -F: '{o="";for(i=1;i<=NF;i++){s=$i;if(length(s)==1)s="0"s;o=o (i>1?":":"") s};print o}')
fi

SSID=$(ipconfig getsummary en0 2>/dev/null | awk -F' SSID : ' '/ SSID :/{print $2; exit}')
NOW=$(date +%s)

sqlite3 "$DB" <<SQL
INSERT OR IGNORE INTO location_samples (ts, gateway_mac, ssid) VALUES ($NOW, '$MAC', '$SSID');
INSERT INTO places (mac, first_seen, last_seen) VALUES ('$MAC', $NOW, $NOW)
    ON CONFLICT(mac) DO UPDATE SET last_seen = $NOW;
SQL

echo "sampled: $MAC (${SSID:-no ssid}) at $(date -r "$NOW" '+%F %T')"

# One-time geolocation for a place we haven't located yet (best-effort:
# silently skipped when Location Services permission is missing).
HAS_GEO=$(sqlite3 "$DB" "SELECT COUNT(*) FROM pragma_table_info('places') WHERE name='lat'")
if [ "$HAS_GEO" = "0" ] || [ -z "$(sqlite3 "$DB" "SELECT lat FROM places WHERE mac='$MAC' AND lat IS NOT NULL" 2>/dev/null)" ]; then
    python3 "$SCRIPT_DIR/locate_places.py" >/dev/null 2>&1 || true
fi
