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
import sqlite3  # noqa: F401

import dbperm
import ssl
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from alias_places import refresh_aliases
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
    # A self-hoster's file can hold their own keys, and it is written by hand
    # under whatever umask happened to be in effect. Tighten it on the way in
    # rather than trusting it — every other file this collector owns is 0600.
    try:
        os.chmod(ENV_FILE, 0o600)
    except OSError:
        pass
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


def is_transit(label):
    """"In transit" is a reserved label for places that are really a moving
    Mac — hotspot stops along a commute. It groups them on the dashboard,
    but it is NOT location consent: unlike other names it never upgrades a
    place to exact coordinates (see the coordinate choice below)."""
    return bool(label) and label.strip().lower() == "in transit"


def is_named(label):
    """The one definition of "the user named this place", shared by the
    rollup, the metadata queries, and the exact-vs-coarse coordinate choice.
    An empty or whitespace label is NOT a name — `label IS NOT NULL` alone
    would count one, and every consumer must agree or a place can be hidden
    in one payload and uploaded in the other."""
    return bool(label and label.strip())


def get_salt():
    """Create-or-read, never truncate: O_EXCL means two concurrent first runs
    cannot overwrite each other's salt — losing it would orphan every hash
    already on the server. The loser of the race reads the winner's file.
    mode=0o600 at open: the file is born private, no chmod window."""
    os.makedirs(os.path.dirname(SALT_FILE), exist_ok=True)
    try:
        fd = os.open(SALT_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(secrets.token_hex(16))
    except FileExistsError:
        pass
    with open(SALT_FILE) as fh:
        salt = fh.read().strip()
    if not salt:                 # lost the race mid-write: reread once
        time.sleep(0.2)
        with open(SALT_FILE) as fh:
            salt = fh.read().strip()
    if not salt:
        raise SystemExit(f"empty salt file {SALT_FILE} — refusing to hash")
    return salt


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
    # One physical place, one bucket: fingerprints within ~200 m of a
    # canonical place (the home router vs the hotspot's grid cell) fold into
    # it here, before hashing — aliases never reach the payload, and the
    # sweep retires their old rows from the server on the next full push.
    aliases = refresh_aliases(db)
    raw_labels = dict(db.execute("SELECT mac, label FROM places"))
    labels = {mac: lbl if is_named(lbl) else mac
              for mac, lbl in raw_labels.items()}
    agg = {}
    for (_rid, ts, source, _proj, model, inp, out, cread, ccre, pkey) in rows:
        # The strict opt-out has to hold here, in the hourly rows, not only in
        # the metadata query: uploading usage keyed by an unnamed place's hash
        # would still let the server watch that place's visiting pattern. So
        # the pkey itself is dropped and the bucket merges into "unknown" —
        # totals stay intact, the place does not exist remotely at all.
        # Judged on the ORIGINAL fingerprint, before alias resolution: an
        # unnamed hotspot that merged into a named place is still an unnamed
        # fingerprint, and strict mode must not let the merge smuggle its
        # usage under the named place's hash.
        if HIDE_UNNAMED and pkey and not is_named(raw_labels.get(pkey)):
            pkey = None
        else:
            pkey = aliases.get(pkey, pkey)
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


def write_session(session):
    """Atomic replace via a 0600 temp file: the session is born private (no
    chmod window), and a crash mid-write leaves the previous session intact
    instead of a truncated file that strands the machine's auth. The refresh
    token rotates on every use, so a corrupted write here is unrecoverable."""
    tmp = SESSION_FILE + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(session, fh)
    os.replace(tmp, SESSION_FILE)


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
    write_session(session)
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


def api_delete(path, jwt):
    """DELETE via PostgREST. RLS (`user_id = auth.uid()`) scopes every delete to
    the caller's own rows, so a filter can never reach another account."""
    req = urllib.request.Request(
        f"{os.environ['SUPABASE_URL']}/rest/v1/{path}", method="DELETE",
        headers={"apikey": os.environ["SUPABASE_ANON_KEY"],
                 "Authorization": f"Bearer {jwt}",
                 "Prefer": "count=exact"})
    try:
        with urllib.request.urlopen(req, context=SSL_CTX) as resp:
            # content-range is "*/N" for a counted delete.
            rng = resp.headers.get("content-range", "*/0")
            return int(rng.split("/")[-1] or 0)
    except urllib.error.HTTPError as err:
        table = path.split("?")[0]
        raise SystemExit(
            f"sweep of {table} failed: HTTP {err.code} "
            f"{err.read().decode(errors='replace')[:400]}\n"
            "If this is a 403, the schema predates the sweep: apply\n"
            f"  GRANT DELETE ON public.{table} TO authenticated;")


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
    (True, False): "SELECT mac, label, kind, last_seen, lat, lon, geo_name "
                   "FROM places "
                   "WHERE (label IS NOT NULL OR lat IS NOT NULL) "
                   "AND alias_of IS NULL",
    (True, True): "SELECT mac, label, kind, last_seen, lat, lon, geo_name "
                  "FROM places "
                  "WHERE label IS NOT NULL AND TRIM(label) <> '' "
                  "AND alias_of IS NULL",
    (False, True): "SELECT mac, label, kind, last_seen FROM places "
                   "WHERE label IS NOT NULL AND TRIM(label) <> '' "
                   "AND alias_of IS NULL",
    (False, False): "SELECT mac, label, kind, last_seen FROM places "
                    "WHERE label IS NOT NULL AND alias_of IS NULL",
}

SEEN_ROUND_S = 15 * 60


def coarse_time(ts):
    """Round a sample time down to a 15-minute bucket, UTC.

    `last_seen` is not new data — collect_place.sh has written it on every
    5-minute sample since the first release; it simply never left the machine.
    What it buys once uploaded is the dashboard being able to say which place
    you are at *now*, and to tell a collector that is still running from one
    that stopped months ago (rows alone cannot: they prove ownership, not
    recency).

    Rounded because the exact minute you arrived somewhere is a finer trace
    than anything else here — the usage rollups are hourly — and a 15-minute
    bucket answers both questions just as well. Rounded *down*, so the value
    can only ever understate how recently you were seen."""
    if not ts:
        return None
    bucket = (int(ts) // SEEN_ROUND_S) * SEEN_ROUND_S
    return datetime.fromtimestamp(bucket, timezone.utc).isoformat()


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
                 "kind": kind, "last_seen": coarse_time(row[3])}
        if geo and row[4] is not None:
            lat, lon = ((row[4], row[5])
                        if is_named(label) and not is_transit(label)
                        else coarse(row[4], row[5]))
            entry.update({"lat": lat, "lon": lon, "geo_name": row[6]})
        payload.append(entry)

    db.commit()
    if payload:
        api("aiwork_places?on_conflict=user_id,place_hash", payload, jwt)

    # Undo the past too, scoped to hashes this machine knows — never a
    # blanket not-in-payload delete, which would eat another machine's
    # places on a shared account:
    #   * aliased places: their metadata was uploaded back when they were
    #     canonical; the row must come off or the merged place shows twice.
    #   * strict mode: metadata uploaded before AIWORK_HIDE_UNNAMED was set,
    #     or "off the server entirely" is only true for places detected
    #     after the flag.
    retire = [place_hash(mac, salt) for (mac, lbl, alias) in
              db.execute("SELECT mac, label, alias_of FROM places")
              if alias is not None or (HIDE_UNNAMED and not is_named(lbl))]
    for start in range(0, len(retire), 100):
        chunk = ",".join(retire[start:start + 100])
        api_delete(f"aiwork_places?place_hash=in.({chunk})", jwt)

    return len(payload), pulled


def push(db, agg, salt, since_day=None):
    if not os.environ.get("SUPABASE_URL"):
        raise SystemExit("SUPABASE_URL not set (or run with AIWORK_LOCAL_ONLY=1)")
    jwt, uid = authenticate()
    # Stamped by us, not by the server's `now()` default, so the sweep below
    # compares timestamps from one clock. Every row this run writes carries
    # exactly this value; anything older in the window is a row this run did
    # not produce.
    run_at = datetime.now(timezone.utc).isoformat()
    payload = [
        {"user_id": uid, "day": day, "hour": hour,
         "place_hash": place_hash(pkey, salt) if pkey else None,
         "source": source, "model": model, "requests": a[0],
         "input_tokens": a[1], "output_tokens": a[2],
         "cache_read_tokens": a[3], "cache_creation_tokens": a[4],
         "active_minutes": len(a[5]), "uploaded_at": run_at}
        for (day, hour, _place, pkey, source, model), a in sorted(agg.items())
    ]
    # Hourly buckets multiply the row count, so upload in chunks rather than
    # betting on one request body staying under every proxy's limit.
    path = "aiwork_daily?on_conflict=user_id,day,hour,place_hash,source,model"
    status = None
    for start in range(0, len(payload), UPLOAD_CHUNK):
        status = api(path, payload[start:start + UPLOAD_CHUNK], jwt)

    # Sweep: a request's place attribution is not fixed forever. Re-keying a
    # place, hysteresis settling, or a nearer location sample landing inside
    # the 30-minute window all move a request from one place_hash to another.
    # Upsert alone writes the new row and leaves the old one behind, so the
    # server's total drifts above the machine's and the place list stops
    # summing to it. Deleting what this run did not write makes the push
    # authoritative for its window instead of merely additive.
    #
    # Upsert first, then delete, so there is never an instant where a bucket
    # is missing from the server. A crash in between leaves stale rows, which
    # is exactly the state before this ran and is corrected by the next run.
    swept = 0
    if payload:
        stale = f"aiwork_daily?uploaded_at=lt.{urllib.parse.quote(run_at)}"
        if since_day:                      # windowed run: only its own days
            stale += f"&day=gte.{since_day}"
        swept = api_delete(stale, jwt)

    places, pulled = sync_place_labels(db, salt, jwt, uid)
    print(f"pushed {len(payload)} hourly rows (HTTP {status}), "
          f"{places} places"
          + (f", swept {swept} stale rows" if swept else "")
          + (f", {pulled} renamed on the web" if pulled else ""))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--since-hours", type=float, default=None,
                    help="only roll up and upload requests this recent "
                         "(default: the whole history)")
    args = ap.parse_args()
    # Floor to the start of the UTC *day*, not the hour. A bucket is rebuilt
    # from scratch and upserted, so a boundary falling mid-hour would replace
    # that hour's complete row with only the part of it inside the window —
    # silently dropping tokens. The day boundary is the stricter version of the
    # same rule, and it is what the sweep in push() needs: the sweep deletes by
    # day, so every bucket in every day it can reach has to be re-pushed, or it
    # would delete buckets this run never rebuilt. Costs at most 24 extra
    # hours of re-aggregation, which is local work.
    since = (int(time.time() - args.since_hours * 3600) // 86400 * 86400
             if args.since_hours else None)
    since_day = (datetime.fromtimestamp(since, timezone.utc).strftime("%Y-%m-%d")
                 if since else None)

    db = dbperm.connect(DB)
    rows = joined_rows(db, since)
    matched = sum(1 for r in rows if r[9])
    scope = f"last {args.since_hours:g}h" if since else "all history"
    print(f"{len(rows)} requests ({scope}), {matched} matched to a place "
          f"({len(rows) - matched} unknown)")
    agg = rollup(db, rows)
    if LOCAL_ONLY:
        print(f"local-only: report written to {write_csv(agg)}")
    else:
        push(db, agg, get_salt(), since_day)


if __name__ == "__main__":
    main()
