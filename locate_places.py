#!/usr/bin/env python3
"""Geolocate the place you are currently at (one-time per gateway MAC).

Coordinates come from macOS CoreLocation (Apple WiFi positioning, local
permission prompt on first run). The place name comes from Nominatim
(OpenStreetMap reverse geocoding) — both free, no API keys.
"""
import json
import os
import sqlite3
import ssl
import subprocess
import sys
import time
import urllib.request

DB = os.environ.get("AIWORK_DB", os.path.expanduser("~/.aiwork/local.db"))
NOMINATIM = "https://nominatim.openstreetmap.org/reverse"
USER_AGENT = "burninpublic-collector/0.1 (https://github.com/machinefriendly/burninpublic-collector)"

try:
    import certifi
    SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    SSL_CTX = ssl.create_default_context()

GEO_COLUMNS = {
    "lat": "ALTER TABLE places ADD COLUMN lat REAL",
    "lon": "ALTER TABLE places ADD COLUMN lon REAL",
    "accuracy_m": "ALTER TABLE places ADD COLUMN accuracy_m REAL",
    "geo_name": "ALTER TABLE places ADD COLUMN geo_name TEXT",
    "geo_full": "ALTER TABLE places ADD COLUMN geo_full TEXT",
}


def ensure_columns(db):
    have = {r[1] for r in db.execute("PRAGMA table_info(places)")}
    for col, stmt in GEO_COLUMNS.items():
        if col not in have:
            db.execute(stmt)


def current_gateway_mac():
    try:
        gw = subprocess.run(["route", "-n", "get", "default"],
                            capture_output=True, text=True, timeout=10).stdout
        ip = next((l.split(":")[1].strip() for l in gw.splitlines()
                   if "gateway" in l), None)
        if not ip:
            return None
        arp = subprocess.run(["arp", "-n", ip], capture_output=True,
                             text=True, timeout=10).stdout
        raw = arp.split(" at ")[1].split(" ")[0] if " at " in arp else None
        if not raw or raw == "(incomplete)":
            return None
        return ":".join(o.zfill(2) for o in raw.lower().split(":"))
    except (subprocess.SubprocessError, IndexError):
        return None


HELPER = os.path.expanduser("~/.aiwork/bin/aiwork-locate")
APP = os.path.expanduser("~/.aiwork/bin/AiworkLocate.app")
FIX_FILE = os.path.expanduser("~/.aiwork/last_fix.txt")


def core_location_fix(timeout_s=25):
    """Block until CoreLocation delivers a fix. Returns (lat, lon, accuracy).

    Prefers the .app bundle launched via LaunchServices — the only form macOS
    reliably shows a location permission prompt for. Falls back to the bare
    helper binary, then pyobjc."""
    if os.path.isdir(APP):
        try:
            os.remove(FIX_FILE)
        except FileNotFoundError:
            pass
        subprocess.run(["open", "-W", APP], timeout=timeout_s + 20)
        if os.path.exists(FIX_FILE):
            with open(FIX_FILE) as fh:
                lat, lon, acc = fh.read().split()
            return float(lat), float(lon), float(acc)
        raise SystemExit(
            "no fix from AiworkLocate.app — check the permission prompt, or "
            "enable 'AiworkLocate' in System Settings > Privacy & Security > "
            "Location Services, then rerun")
    if os.path.exists(HELPER):
        run = subprocess.run([HELPER], capture_output=True, text=True,
                             timeout=timeout_s + 10)
        if run.returncode == 0 and run.stdout.strip():
            lat, lon, acc = run.stdout.split()
            return float(lat), float(lon), float(acc)
        raise SystemExit(
            f"aiwork-locate failed: {run.stderr.strip() or 'no fix'} — run "
            f"'{HELPER}' once in Terminal.app and allow the location prompt")
    import CoreLocation
    from Foundation import NSDate, NSRunLoop

    if not CoreLocation.CLLocationManager.locationServicesEnabled():
        raise SystemExit("Location Services are off — enable them in "
                         "System Settings > Privacy & Security > Location Services")
    manager = CoreLocation.CLLocationManager.alloc().init()
    status = manager.authorizationStatus()
    if status == 0:  # kCLAuthorizationStatusNotDetermined → trigger the prompt
        manager.requestWhenInUseAuthorization()
    elif status in (1, 2):  # restricted / denied
        raise SystemExit(
            "location access denied for this terminal app — allow it in "
            "System Settings > Privacy & Security > Location Services")
    manager.startUpdatingLocation()
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        NSRunLoop.currentRunLoop().runUntilDate_(
            NSDate.dateWithTimeIntervalSinceNow_(0.25))
        loc = manager.location()
        if loc is not None and loc.horizontalAccuracy() >= 0:
            c = loc.coordinate()
            return c.latitude, c.longitude, loc.horizontalAccuracy()
    raise TimeoutError(
        "no CoreLocation fix — grant Location Services to your terminal in "
        "System Settings > Privacy & Security > Location Services, then rerun")


def reverse_geocode(lat, lon):
    """Nominatim usage policy: 1 req/s max, identify with a User-Agent."""
    url = f"{NOMINATIM}?lat={lat}&lon={lon}&format=jsonv2&zoom=16"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, context=SSL_CTX, timeout=15) as resp:
        data = json.load(resp)
    addr = data.get("address", {})
    short = ", ".join(
        p for p in (
            addr.get("neighbourhood") or addr.get("suburb")
            or addr.get("road") or addr.get("village"),
            addr.get("city") or addr.get("town") or addr.get("municipality"),
        ) if p)
    return short or data.get("name", ""), data.get("display_name", "")


def main():
    force = "--force" in sys.argv
    db = sqlite3.connect(DB)
    ensure_columns(db)
    mac = current_gateway_mac()
    if not mac:
        raise SystemExit("no gateway detected (offline?)")
    row = db.execute("SELECT label, lat FROM places WHERE mac=?", (mac,)).fetchone()
    if row is None:
        raise SystemExit(f"{mac} not sampled yet — run collect_place.sh first")
    if row[1] is not None and not force:
        print(f"{row[0] or mac} already located (use --force to redo)")
        return

    lat, lon, acc = core_location_fix()
    geo_name, geo_full = reverse_geocode(lat, lon)
    db.execute("UPDATE places SET lat=?, lon=?, accuracy_m=?, geo_name=?, "
               "geo_full=? WHERE mac=?", (lat, lon, acc, geo_name, geo_full, mac))
    db.commit()
    print(f"{row[0] or mac} → {geo_name}  (±{acc:.0f} m)")
    print(f"  {geo_full}")
    print(f"  https://www.google.com/maps/search/?api=1&query={lat:.5f},{lon:.5f}")


if __name__ == "__main__":
    main()
