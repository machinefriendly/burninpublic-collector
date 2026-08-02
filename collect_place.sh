#!/bin/bash
# Location sampler, every 5 min. The place fingerprint is the default gateway's
# MAC — bolted to a building, free to read, no location services. When the
# gateway is portable (phone hotspot, VPN with no next-hop MAC) that assumption
# fails and place_key.py resolves a coarse CoreLocation grid cell instead.
set -euo pipefail
# The DB (and sqlite's -wal/-shm side files) hold raw place keys and SSIDs —
# born 0600, not chmod'd after the fact.
umask 077

DB="${AIWORK_DB:-$HOME/.aiwork/local.db}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

mkdir -p "$(dirname "$DB")"
sqlite3 "$DB" < "$SCRIPT_DIR/local_schema.sql"

# Single route lookup: we need the gateway *and* the interface it goes out of
# (assuming en0 breaks Ethernet, USB tethering and multi-homed setups).
ROUTE=$(route -n get default 2>/dev/null || true)
GW_IP=$(printf "%s" "$ROUTE" | awk '/gateway:/{print $2; exit}')
IFACE=$(printf "%s" "$ROUTE" | awk '/interface:/{print $2; exit}')
if [ -z "${GW_IP:-}" ]; then
    echo "no default gateway (offline?) — nothing sampled" >&2
    exit 0
fi

ARP=$(arp -n "$GW_IP" 2>/dev/null || true)   # exits 1 on "no entry"; not fatal
RAW_MAC=$(printf "%s" "$ARP" | awk '{for(i=1;i<=NF;i++) if($i=="at"){print $(i+1); exit}}')
MAC=""
if [ -n "${RAW_MAC:-}" ] && [ "$RAW_MAC" != "(incomplete)" ]; then
    # macOS arp drops leading zeros (a:b:c:...) — normalize to aa:bb:cc:dd:ee:ff
    MAC=$(echo "$RAW_MAC" | awk -F: '{for(i=1;i<=NF;i++) printf "%s%02x", (i>1?":":""), strtonum("0x"$i)}' 2>/dev/null || true)
    if [ -z "$MAC" ]; then
        MAC=$(echo "$RAW_MAC" | tr 'A-F' 'a-f' | awk -F: '{o="";for(i=1;i<=NF;i++){s=$i;if(length(s)==1)s="0"s;o=o (i>1?":":"") s};print o}')
    fi
fi

SSID=""
if [ -n "${IFACE:-}" ]; then
    SSID=$(ipconfig getsummary "$IFACE" 2>/dev/null | awk -F' SSID : ' '/ SSID :/{print $2; exit}' || true)
fi

# Portable gateway? iOS hotspots hand out 172.20.10.0/28; a locally-administered
# MAC (2nd hex digit 2/6/a/e) means a phone hotspot or a randomised/virtual
# interface; no resolvable MAC at all means a VPN tunnel. None of these are
# tied to a building, so the MAC would merge every place you work from into one.
# (`[ ... ] && x=1` would abort the script under `set -e` whenever the test is
# false, so every signal is checked with an explicit if/case.)
PORTABLE=0
case "$GW_IP" in 172.20.10.*) PORTABLE=1 ;; esac
case "$MAC" in ?[26ae]:*) PORTABLE=1 ;; esac
if [ -z "$MAC" ]; then PORTABLE=1; fi

PLACE_KEY="$MAC"
if [ "$PORTABLE" = "1" ]; then
    GEO_KEY=$(python3 "$SCRIPT_DIR/place_key.py" --gateway-mac "$MAC" \
                  --ssid "$SSID" 2>/dev/null || true)
    if [ -n "${GEO_KEY:-}" ]; then
        PLACE_KEY="$GEO_KEY"
    elif [ -z "$MAC" ]; then
        echo "portable gateway, no location fix — nothing sampled" >&2
        exit 0
    else
        echo "portable gateway, no location fix — falling back to MAC" >&2
    fi
fi

NOW=$(date +%s)
esc() { printf "%s" "${1:-}" | sed "s/'/''/g"; }
Q_KEY=$(esc "$PLACE_KEY")
Q_SSID=$(esc "$SSID")

sqlite3 "$DB" <<SQL
INSERT OR IGNORE INTO location_samples (ts, gateway_mac, ssid) VALUES ($NOW, '$Q_KEY', '$Q_SSID');
INSERT INTO places (mac, first_seen, last_seen) VALUES ('$Q_KEY', $NOW, $NOW)
    ON CONFLICT(mac) DO UPDATE SET last_seen = $NOW;
SQL

# Deliberately no place key, MAC, or SSID here: this line lands in the launchd
# log, and a log is the easiest thing on the machine to leak accidentally.
echo "sampled a place at $(date -r "$NOW" '+%F %T')"

# One-time geolocation for a place we haven't located yet (best-effort:
# silently skipped when Location Services permission is missing). Grid places
# are already located by place_key.py, so this only fires for real routers.
#
# Failed attempts back off for 6 hours. Without this, a place that cannot be
# located (permission undecided, no fix indoors) re-opens AiworkLocate.app on
# every 5-minute sample — which on a machine without a standing grant means a
# location permission popup every 5 minutes, forever.
ATTEMPT="$HOME/.aiwork/.locate_attempt"
HAS_GEO=$(sqlite3 "$DB" "SELECT COUNT(*) FROM pragma_table_info('places') WHERE name='lat'")
if [ "$HAS_GEO" = "0" ] || [ -z "$(sqlite3 "$DB" "SELECT lat FROM places WHERE mac='$Q_KEY' AND lat IS NOT NULL" 2>/dev/null)" ]; then
    LAST=$(stat -f %m "$ATTEMPT" 2>/dev/null || echo 0)
    if [ $((NOW - LAST)) -ge 21600 ]; then
        touch "$ATTEMPT"
        if python3 "$SCRIPT_DIR/locate_places.py" >/dev/null 2>&1; then
            rm -f "$ATTEMPT"       # success: next new place may locate at once
        fi
    fi
fi
