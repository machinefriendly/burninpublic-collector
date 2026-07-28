#!/usr/bin/env python3
"""As-of join, active-time rollup, salted-hash upload to Supabase.

    python3 join_and_push.py                  # full history (nightly pass)
    python3 join_and_push.py --since-hours 48 # only recent buckets

AIWORK_LOCAL_ONLY=1 skips the upload and writes the joined report to CSV
so you can inspect before anything leaves the machine.
"""
import argparse
import csv
import hashlib
import json
import os
import secrets
import sqlite3
import ssl
import time
import urllib.request
from datetime import datetime, timezone

from place_key import cell_centre, grid_cell

try:  # macOS python.org builds ship without system CA certs
    import certifi
    SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    SSL_CTX = ssl.create_default_context()

DB = os.environ.get("AIWORK_DB", os.path.expanduser("~/.aiwork/local.db"))
SALT_FILE = os.path.expanduser("~/.aiwork/salt")
ENV_FILE = os.path.expanduser("~/.aiwork/supabase.env")
SESSION_FILE = os.path.expanduser("~/.aiwork/session.json")


def load_env_file():
    """SUPABASE_URL / SUPABASE_KEY from ~/.aiwork/supabase.env unless already set."""
    if not os.path.exists(ENV_FILE):
        return
    with open(ENV_FILE) as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                os.environ.setdefault(key, val)


load_env_file()
# Hosted burninpublic.com backend by default; the anon key is public by
# design (row-level security is the real protection). Self-hosters override
# both via ~/.aiwork/supabase.env.
os.environ.setdefault("SUPABASE_URL", "https://fuicenrcljloczyvkqsg.supabase.co")
os.environ.setdefault(
    "SUPABASE_ANON_KEY",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIs"
    "InJlZiI6ImZ1aWNlbnJjbGpsb2N6eXZrcXNnIiwicm9sZSI6ImFub24iLCJp"
    "YXQiOjE3ODQ5MTc2MzgsImV4cCI6MjEwMDQ5MzYzOH0."
    "D3WTP_WTiRQVSMEXrM_LI3Gf_nND_45WBboakrPZEYw")
JOIN_TOLERANCE = 30 * 60          # request matches a sample up to 30 min older
UPLOAD_CHUNK = 500                # rows per POST
LOCAL_ONLY = os.environ.get("AIWORK_LOCAL_ONLY") == "1"
# Places you never named are uploaded at grid resolution so they can appear on
# the map and be named from there. Set this to keep them off the server
# entirely — then a place only exists remotely once you name it.
HIDE_UNNAMED = os.environ.get("AIWORK_HIDE_UNNAMED") == "1"
REPORT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")


def get_salt():
    if not os.path.exists(SALT_FILE):
        os.makedirs(os.path.dirname(SALT_FILE), exist_ok=True)
        with open(SALT_FILE, "w") as fh:
            fh.write(secrets.token_hex(16))
        os.chmod(SALT_FILE, 0o600)
    with open(SALT_FILE) as fh:
        return fh.read().strip()


def place_hash(place_key, salt):
    """Salted hash of a place fingerprint (a gateway MAC or a geo:<v>:<cell>).

    The hash input is the raw place key, unchanged since the first release —
    rewriting it would orphan every row and label already on the server."""
    return hashlib.sha256(f"{salt}:{place_key}".encode()).hexdigest()[:16]


_JOIN_SELECT = """
    SELECT u.request_id, u.ts, u.source, u.project, u.model,
           u.input_tokens, u.output_tokens,
           u.cache_read_tokens, u.cache_creation_tokens,
           (SELECT s.gateway_mac FROM location_samples s
             WHERE s.ts <= u.ts AND s.ts >= u.ts - ?
             ORDER BY s.ts DESC LIMIT 1) AS place_key
    FROM usage_requests u
"""
# Usage from before the collector existed can never be attributed to a place,
# so it is not uploaded at all. Otherwise the headline total counts months of
# history while the place list only covers the days since install, and the two
# cannot be reconciled by anyone looking at them. Literal statements, never
# f-strings — the security scanner rejects interpolated SQL (CWE-89).
_TRACKED = " WHERE u.ts >= (SELECT MIN(ts) FROM location_samples)"
JOIN_ALL = _JOIN_SELECT + _TRACKED + " ORDER BY u.ts"
JOIN_SINCE = _JOIN_SELECT + _TRACKED + " AND u.ts >= ? ORDER BY u.ts"


def joined_rows(db, since_ts=None):
    """Per request since tracking began: as-of join to the latest sample <= ts.

    Requests inside the tracking window that still find no sample (machine
    asleep, sampler missed a beat) keep a NULL place and ARE uploaded. Dropping
    them would under-report real usage and hide a collector fault; the
    dashboard shows them as an explicit unmapped row so the places still sum to
    the headline.

    With no samples yet, MIN(ts) is NULL and the comparison yields no rows —
    correct: nothing can be attributed to a place until sampling has started.

    `since_ts` limits the pass to recent requests. Hour buckets are disjoint, so
    a windowed run re-computes whole buckets and the upsert replaces them —
    which is what makes a 15-minute cadence cheap enough to be worth running."""
    if since_ts is None:
        return db.execute(JOIN_ALL, (JOIN_TOLERANCE,)).fetchall()
    return db.execute(JOIN_SINCE, (JOIN_TOLERANCE, since_ts)).fetchall()


def rollup(db, rows):
    """UTC hour x place x source x model, active time = distinct request minutes.

    Buckets are UTC hours, not local days, for two reasons: the machine's
    timezone may not match where you physically are, and a day-level aggregate
    can never be re-cut across a timezone boundary — so the dashboard could not
    show days in the viewer's own timezone. Hour buckets are disjoint, so
    summing active_minutes across them stays correct."""
    labels = dict(db.execute("SELECT mac, COALESCE(label, mac) FROM places"))
    agg = {}
    for (_rid, ts, source, _proj, model, inp, out, cread, ccre, pkey) in rows:
        utc = datetime.fromtimestamp(ts, timezone.utc)
        place = labels.get(pkey, pkey) if pkey else "unknown"
        key = (utc.strftime("%Y-%m-%d"), utc.hour, place, pkey or "",
               source, model or "")
        a = agg.setdefault(key, [0, 0, 0, 0, 0, set()])
        a[0] += 1
        a[1] += inp
        a[2] += out
        a[3] += cread
        a[4] += ccre
        a[5].add(ts // 60)
    return agg


def write_csv(agg):
    os.makedirs(REPORT_DIR, exist_ok=True)
    path = os.path.join(REPORT_DIR, "token_location_report.csv")
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["date_utc", "hour_utc", "place", "source", "model",
                    "requests", "input_tokens", "output_tokens",
                    "cache_read_tokens", "cache_creation_tokens",
                    "total_tokens", "active_minutes"])
        for (day, hour, place, _pkey, source, model), a in sorted(agg.items()):
            w.writerow([day, hour, place, source, model, a[0], a[1], a[2],
                        a[3], a[4], a[1] + a[2] + a[3] + a[4], len(a[5])])
    return path


def authenticate():
    """Refresh-token -> short-lived user JWT. Returns (jwt, user_id).

    The access token is cached until shortly before it expires. Refresh tokens
    rotate on every use and the old one dies immediately, so a run interrupted
    mid-rotation can strand the machine — worth avoiding now that syncs are
    frequent rather than nightly."""
    anon = os.environ.get("SUPABASE_ANON_KEY")
    if not anon or not os.path.exists(SESSION_FILE):
        raise SystemExit("not logged in — run: python3 ~/.aiwork/bin/login.py")
    with open(SESSION_FILE) as fh:
        session = json.load(fh)
    cached, expires = session.get("access_token"), session.get("access_expires", 0)
    if cached and session.get("user_id") and expires - time.time() > 300:
        return cached, session["user_id"]
    req = urllib.request.Request(
        f"{os.environ['SUPABASE_URL']}/auth/v1/token?grant_type=refresh_token",
        data=json.dumps({"refresh_token": session["refresh_token"]}).encode(),
        headers={"apikey": anon, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, context=SSL_CTX) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as err:
        raise SystemExit(
            f"sign-in expired (HTTP {err.code} "
            f"{err.read().decode(errors='replace')[:200]}) — reconnect with: "
            "python3 ~/.aiwork/bin/login.py")
    session.update({
        "refresh_token": data["refresh_token"],
        "user_id": data["user"]["id"],
        "access_token": data["access_token"],
        "access_expires": time.time() + data.get("expires_in", 3600),
    })
    with open(SESSION_FILE, "w") as fh:
        json.dump(session, fh)
    os.chmod(SESSION_FILE, 0o600)
    return data["access_token"], data["user"]["id"]


def api_get(path, jwt):
    req = urllib.request.Request(
        f"{os.environ['SUPABASE_URL']}/rest/v1/{path}",
        headers={"apikey": os.environ["SUPABASE_ANON_KEY"],
                 "Authorization": f"Bearer {jwt}"})
    try:
        with urllib.request.urlopen(req, context=SSL_CTX) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as err:
        raise SystemExit(
            f"read from {path.split('?')[0]} failed: HTTP {err.code} "
            f"{err.read().decode(errors='replace')[:400]}")


def api(path, payload, jwt):
    req = urllib.request.Request(
        f"{os.environ['SUPABASE_URL']}/rest/v1/{path}",
        data=json.dumps(payload).encode(), method="POST",
        headers={"apikey": os.environ["SUPABASE_ANON_KEY"],
                 "Authorization": f"Bearer {jwt}",
                 "Content-Type": "application/json",
                 "Prefer": "resolution=merge-duplicates"})
    try:
        with urllib.request.urlopen(req, context=SSL_CTX) as resp:
            return resp.status
    except urllib.error.HTTPError as err:
        # PostgREST explains itself in the body; a bare "HTTP Error 400" in the
        # nightly log tells you nothing (e.g. a schema migration not applied yet).
        raise SystemExit(
            f"upload to {path.split('?')[0]} failed: HTTP {err.code} "
            f"{err.read().decode(errors='replace')[:400]}")


# Literal statements, selected by (geo columns present, unnamed places hidden).
# Assembling this SQL from fragments would read as an injection site to any
# reviewer or scanner even though every fragment is a constant.
PLACE_QUERIES = {
    (True, False): "SELECT mac, label, kind, lat, lon, geo_name FROM places "
                   "WHERE label IS NOT NULL OR lat IS NOT NULL",
    (True, True): "SELECT mac, label, kind, lat, lon, geo_name FROM places "
                  "WHERE label IS NOT NULL",
    (False, True): "SELECT mac, label, kind FROM places "
                   "WHERE label IS NOT NULL",
    (False, False): "SELECT mac, label, kind FROM places "
                    "WHERE label IS NOT NULL",
}


def coarse(lat, lon):
    """Snap a fix to the same grid the portable-gateway fingerprint uses.

    What leaves the machine for a place you have not named is which cell you
    worked in, not where in it. Idempotent for grid places — their stored
    coordinates are already a cell centre."""
    return cell_centre(grid_cell(lat, lon))


def ensure_label_shadow(db):
    """`label_synced` mirrors the label as the server last knew it.

    Comparing local label / shadow / remote label is what lets renaming work
    from either side without a clock: whichever side differs from the shadow is
    the side that changed."""
    have = {r[1] for r in db.execute("PRAGMA table_info(places)")}
    if "label_synced" not in have:
        db.execute("ALTER TABLE places ADD COLUMN label_synced TEXT")
        db.commit()


def sync_place_labels(db, salt, jwt, uid):
    """Reconcile labels with the server, then upload places.

    Labels can be set locally (places.py) or remotely (rename on the web), so
    this is a three-way merge against `label_synced`:
      * local differs from shadow  → the machine renamed it, push it up
      * remote differs from shadow → the web renamed it, pull it down
      * both differ                → the web wins (it is the newer surface and
        the only one a user can reach from another device)

    Coordinates: exact for places you named, grid-coarse for the rest, and
    absent entirely under AIWORK_HIDE_UNNAMED=1."""
    ensure_label_shadow(db)
    remote = {r["place_hash"]: (r.get("label") or "")
              for r in api_get("aiwork_places?select=place_hash,label", jwt)}

    have = {r[1] for r in db.execute("PRAGMA table_info(places)")}
    geo = {"lat", "lon", "geo_name"}.issubset(have)
    rows = db.execute(PLACE_QUERIES[(geo, HIDE_UNNAMED)]).fetchall()

    payload, pulled = [], 0
    for row in rows:
        key, label, kind = row[0], row[1] or "", row[2]
        phash = place_hash(key, salt)
        shadow = db.execute("SELECT label_synced FROM places WHERE mac = ?",
                            (key,)).fetchone()[0] or ""
        remote_label = remote.get(phash, "")
        if remote_label and remote_label != shadow and remote_label != label:
            label = remote_label            # renamed on the web → adopt it
            db.execute("UPDATE places SET label = ? WHERE mac = ?", (label, key))
            pulled += 1
        db.execute("UPDATE places SET label_synced = ? WHERE mac = ?",
                   (label, key))

        entry = {"user_id": uid, "place_hash": phash, "label": label,
                 "kind": kind}
        if geo and row[3] is not None:
            lat, lon = (row[3], row[4]) if label else coarse(row[3], row[4])
            entry.update({"lat": lat, "lon": lon, "geo_name": row[5]})
        payload.append(entry)

    db.commit()
    if payload:
        api("aiwork_places?on_conflict=user_id,place_hash", payload, jwt)
    return len(payload), pulled


def push(db, agg, salt):
    if not os.environ.get("SUPABASE_URL"):
        raise SystemExit("SUPABASE_URL not set (or run with AIWORK_LOCAL_ONLY=1)")
    jwt, uid = authenticate()
    payload = [
        {"user_id": uid, "day": day, "hour": hour,
         "place_hash": place_hash(pkey, salt) if pkey else None,
         "source": source, "model": model, "requests": a[0],
         "input_tokens": a[1], "output_tokens": a[2],
         "cache_read_tokens": a[3], "cache_creation_tokens": a[4],
         "active_minutes": len(a[5])}
        for (day, hour, _place, pkey, source, model), a in sorted(agg.items())
    ]
    # Hourly buckets multiply the row count, so upload in chunks rather than
    # betting on one request body staying under every proxy's limit.
    path = "aiwork_daily?on_conflict=user_id,day,hour,place_hash,source,model"
    status = None
    for start in range(0, len(payload), UPLOAD_CHUNK):
        status = api(path, payload[start:start + UPLOAD_CHUNK], jwt)
    places, pulled = sync_place_labels(db, salt, jwt, uid)
    print(f"pushed {len(payload)} hourly rows (HTTP {status}), "
          f"{places} places" + (f", {pulled} renamed on the web" if pulled else ""))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--since-hours", type=float, default=None,
                    help="only roll up and upload requests this recent "
                         "(default: the whole history)")
    args = ap.parse_args()
    # Floor to the UTC hour. A bucket is rebuilt from scratch and upserted, so
    # a boundary falling mid-hour would replace that hour's complete row with
    # only the part of it inside the window — silently dropping tokens.
    since = (int(time.time() - args.since_hours * 3600) // 3600 * 3600
             if args.since_hours else None)

    db = sqlite3.connect(DB)
    rows = joined_rows(db, since)
    matched = sum(1 for r in rows if r[9])
    scope = f"last {args.since_hours:g}h" if since else "all history"
    print(f"{len(rows)} requests ({scope}), {matched} matched to a place "
          f"({len(rows) - matched} unknown)")
    agg = rollup(db, rows)
    if LOCAL_ONLY:
        print(f"local-only: report written to {write_csv(agg)}")
    else:
        push(db, agg, get_salt())


if __name__ == "__main__":
    main()
