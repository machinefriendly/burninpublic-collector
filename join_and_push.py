#!/usr/bin/env python3
"""As-of join, active-time rollup, salted-hash upload to Supabase.

AIWORK_LOCAL_ONLY=1 skips the upload and writes the joined report to CSV
so you can inspect before anything leaves the machine.
"""
import csv
import hashlib
import json
import os
import secrets
import sqlite3
import urllib.request
from datetime import datetime

DB = os.environ.get("AIWORK_DB", os.path.expanduser("~/.aiwork/local.db"))
SALT_FILE = os.path.expanduser("~/.aiwork/salt")
JOIN_TOLERANCE = 30 * 60          # request matches a sample up to 30 min older
LOCAL_ONLY = os.environ.get("AIWORK_LOCAL_ONLY") == "1"
REPORT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")


def get_salt():
    if not os.path.exists(SALT_FILE):
        os.makedirs(os.path.dirname(SALT_FILE), exist_ok=True)
        with open(SALT_FILE, "w") as fh:
            fh.write(secrets.token_hex(16))
        os.chmod(SALT_FILE, 0o600)
    with open(SALT_FILE) as fh:
        return fh.read().strip()


def place_hash(mac, salt):
    return hashlib.sha256(f"{salt}:{mac}".encode()).hexdigest()[:16]


def joined_rows(db):
    """Per request: as-of join to the latest location sample <= ts."""
    return db.execute("""
        SELECT u.request_id, u.ts, u.source, u.project, u.model,
               u.input_tokens, u.output_tokens,
               u.cache_read_tokens, u.cache_creation_tokens,
               (SELECT s.gateway_mac FROM location_samples s
                 WHERE s.ts <= u.ts AND s.ts >= u.ts - ?
                 ORDER BY s.ts DESC LIMIT 1) AS mac
        FROM usage_requests u ORDER BY u.ts
    """, (JOIN_TOLERANCE,)).fetchall()


def rollup(db, rows):
    """day x place x source x model, active time = distinct request minutes."""
    labels = dict(db.execute("SELECT mac, COALESCE(label, mac) FROM places"))
    agg = {}
    for (_rid, ts, source, _proj, model, inp, out, cread, ccre, mac) in rows:
        day = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
        place = labels.get(mac, mac) if mac else "unknown"
        key = (day, place, mac or "", source, model or "")
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
        w.writerow(["date", "place", "source", "model", "requests",
                    "input_tokens", "output_tokens", "cache_read_tokens",
                    "cache_creation_tokens", "total_tokens", "active_minutes"])
        for (day, place, _mac, source, model), a in sorted(agg.items()):
            w.writerow([day, place, source, model, a[0], a[1], a[2], a[3],
                        a[4], a[1] + a[2] + a[3] + a[4], len(a[5])])
    return path


def push(agg, salt):
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not url or not key:
        raise SystemExit("SUPABASE_URL / SUPABASE_KEY not set "
                         "(or run with AIWORK_LOCAL_ONLY=1)")
    payload = [
        {"day": day, "place_hash": place_hash(mac, salt) if mac else None,
         "source": source, "model": model, "requests": a[0],
         "input_tokens": a[1], "output_tokens": a[2],
         "cache_read_tokens": a[3], "cache_creation_tokens": a[4],
         "active_minutes": len(a[5])}
        for (day, _place, mac, source, model), a in sorted(agg.items())
    ]
    req = urllib.request.Request(
        f"{url}/rest/v1/aiwork_daily?on_conflict=day,place_hash,source,model",
        data=json.dumps(payload).encode(), method="POST",
        headers={"apikey": key, "Authorization": f"Bearer {key}",
                 "Content-Type": "application/json",
                 "Prefer": "resolution=merge-duplicates"})
    with urllib.request.urlopen(req) as resp:
        print(f"pushed {len(payload)} rows (HTTP {resp.status})")


def main():
    db = sqlite3.connect(DB)
    rows = joined_rows(db)
    matched = sum(1 for r in rows if r[9])
    print(f"{len(rows)} requests, {matched} matched to a place "
          f"({len(rows) - matched} unknown)")
    agg = rollup(db, rows)
    if LOCAL_ONLY:
        print(f"local-only: report written to {write_csv(agg)}")
    else:
        push(agg, get_salt())


if __name__ == "__main__":
    main()
