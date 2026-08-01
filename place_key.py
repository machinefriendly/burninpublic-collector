#!/usr/bin/env python3
"""Place fingerprint for gateways whose MAC is not tied to a building.

A stable router's MAC is a free, privacy-light stand-in for "where you are":
it is bolted to a building, so same MAC == same place, no location services
needed. A phone hotspot breaks that assumption — the gateway is your phone, it
travels with you, and every place you work from collapses into one fingerprint.
For those gateways we fall back to a coarse CoreLocation grid cell.

Prints the resolved place key (``geo:<version>:<row>_<col>``) on stdout, or
exits non-zero so the caller can fall back to the MAC. Raw fixes are kept in
the local DB only; the ``places`` row gets the *cell centre*, never the exact
fix, so even a place you later name and sync stays coarse.
"""
import argparse
import math
import os
import sqlite3  # noqa: F401

import dbperm
import time

from locate_places import core_location_fix, ensure_columns, reverse_geocode

DB = os.environ.get("AIWORK_DB", os.path.expanduser("~/.aiwork/local.db"))

# Grid size. 250 m keeps neighbouring venues apart without inventing places out
# of indoor fix noise; below ~150 m the cell flaps faster than hysteresis can
# damp it. Changing this changes what a cell *means*, so KEY_VERSION must be
# bumped with it — old keys keep their old meaning forever.
GRID_M = int(os.environ.get("AIWORK_GRID_M", "250"))
KEY_VERSION = 1
# How long a fix is reused before taking another. Each fix opens
# AiworkLocate.app, so this is the battery/annoyance dial. A network change
# forces a fresh fix regardless.
FIX_TTL = int(os.environ.get("AIWORK_FIX_TTL_MIN", "30")) * 60

M_PER_DEG_LAT = 111320.0
HALF_DIAGONAL_M = GRID_M * math.sqrt(2) / 2

# iOS Personal Hotspot always hands out 172.20.10.0/28 with the phone at .1.
HOTSPOT_PREFIX = "172.20.10."


def is_portable_gateway(gw_ip, mac):
    """True when the gateway MAC cannot be trusted as a place fingerprint.

    Three signals, any of which is enough:
      * the iOS Personal Hotspot subnet;
      * a locally-administered MAC (2nd hex digit 2/6/a/e) — phone hotspots and
        randomised/virtual interfaces, none of them bolted to a building;
      * a default route with no ARP-resolvable next hop (VPN tunnels), where
        there is no MAC to fingerprint at all.
    False positives cost a location fix, not correctness: a stable router that
    happens to use a locally-administered MAC still yields a stable cell."""
    if not mac:
        return True
    if gw_ip and gw_ip.startswith(HOTSPOT_PREFIX):
        return True
    return len(mac) > 1 and mac[1] in "26ae"


def lon_step(row):
    """Longitude degrees per cell at this row's latitude (cells shrink poleward)."""
    lat_centre = (row + 0.5) * (GRID_M / M_PER_DEG_LAT)
    scale = max(math.cos(math.radians(lat_centre)), 0.01)
    return GRID_M / (M_PER_DEG_LAT * scale)


def grid_cell(lat, lon):
    row = math.floor(lat / (GRID_M / M_PER_DEG_LAT))
    return f"{row}_{math.floor(lon / lon_step(row))}"


def cell_centre(cell):
    row, col = (int(x) for x in cell.split("_"))
    return ((row + 0.5) * (GRID_M / M_PER_DEG_LAT),
            (col + 0.5) * lon_step(row))


def distance_m(lat1, lon1, lat2, lon2):
    dlat = (lat2 - lat1) * M_PER_DEG_LAT
    dlon = ((lon2 - lon1) * M_PER_DEG_LAT
            * math.cos(math.radians((lat1 + lat2) / 2)))
    return math.hypot(dlat, dlon)


def settled_cell(prev_cell, lat, lon, accuracy_m):
    """Hysteresis: stay in the previous cell while the fix could still be in it.

    A stationary machine near a cell edge would otherwise flap between adjacent
    cells on fix noise alone, inventing places that don't exist. We only move
    once the fix is further from the old cell's centre than the cell's own
    reach, even after allowing for the fix's accuracy."""
    cell = grid_cell(lat, lon)
    if not prev_cell or prev_cell == cell:
        return cell
    plat, plon = cell_centre(prev_cell)
    if distance_m(lat, lon, plat, plon) - (accuracy_m or 0) <= HALF_DIAGONAL_M:
        return prev_cell
    return cell


def load_state(db):
    row = db.execute("SELECT ts, cell, net_sig FROM geo_state "
                     "WHERE id = 1").fetchone()
    return {"ts": row[0], "cell": row[1], "net_sig": row[2]} if row else None


def save_state(db, ts, lat, lon, accuracy_m, cell, net_sig):
    db.execute(
        "INSERT INTO geo_state (id, ts, lat, lon, accuracy_m, cell, net_sig) "
        "VALUES (1, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
        "ts = excluded.ts, lat = excluded.lat, lon = excluded.lon, "
        "accuracy_m = excluded.accuracy_m, cell = excluded.cell, "
        "net_sig = excluded.net_sig",
        (ts, lat, lon, accuracy_m, cell, net_sig))
    db.commit()


def describe_cell(db, key, cell):
    """Give the cell a coarse location + OSM name, once, for the local UI.

    Stores the cell *centre* rather than the fix: a named place that the user
    chooses to sync then leaks only which 250 m cell it was, not where in it."""
    ensure_columns(db)
    row = db.execute("SELECT lat FROM places WHERE mac = ?", (key,)).fetchone()
    if row and row[0] is not None:
        return
    lat, lon = cell_centre(cell)
    try:
        geo_name, geo_full = reverse_geocode(lat, lon)
    except Exception:                        # offline / Nominatim down / rate limit
        geo_name, geo_full = "", ""
    now = int(time.time())
    db.execute("INSERT OR IGNORE INTO places (mac, first_seen, last_seen) "
               "VALUES (?, ?, ?)", (key, now, now))
    db.execute("UPDATE places SET lat = ?, lon = ?, accuracy_m = ?, "
               "geo_name = ?, geo_full = ? WHERE mac = ?",
               (lat, lon, HALF_DIAGONAL_M, geo_name or None,
                geo_full or None, key))
    db.commit()


def resolve(db, net_sig, force=False):
    """Current grid place key, taking a fresh fix only when we have to."""
    state = load_state(db)
    now = int(time.time())
    if (state and not force and state["cell"]
            and state["net_sig"] == net_sig
            and now - state["ts"] < FIX_TTL):
        return f"geo:{KEY_VERSION}:{state['cell']}", state["cell"]
    lat, lon, accuracy_m = core_location_fix()
    cell = settled_cell(state["cell"] if state else None, lat, lon, accuracy_m)
    save_state(db, now, lat, lon, accuracy_m, cell, net_sig)
    return f"geo:{KEY_VERSION}:{cell}", cell


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--gateway-mac", default="")
    ap.add_argument("--ssid", default="")
    ap.add_argument("--force", action="store_true",
                    help="take a fresh fix even if the cached one is fresh")
    args = ap.parse_args()

    db = dbperm.connect(DB)
    key, cell = resolve(db, f"{args.gateway_mac}|{args.ssid}", args.force)
    describe_cell(db, key, cell)
    print(key)


if __name__ == "__main__":
    main()
