#!/usr/bin/env python3
"""List and name detected places.

    python3 places.py                             # list
    python3 places.py aa:bb:cc:dd:ee:ff home home # set label + kind
    python3 places.py geo:1:11856_47390 cafe cafe # same, grid-cell place

A place key is either a gateway MAC (a router, bolted to a building) or a
geo:<version>:<cell> grid cell for portable gateways — see place_key.py.
Naming a place is also what allows it to be uploaded at all: unnamed places
stay entirely local.
"""
import os
import sqlite3  # noqa: F401

import dbperm
import sys
from datetime import datetime

DB = os.environ.get("AIWORK_DB", os.path.expanduser("~/.aiwork/local.db"))


def fmt(ts):
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else "-"


def main():
    db = dbperm.connect(DB)
    if len(sys.argv) >= 3:
        key = sys.argv[1].lower()
        label = sys.argv[2]
        kind = sys.argv[3] if len(sys.argv) > 3 else None
        cur = db.execute("UPDATE places SET label=?, kind=? WHERE mac=?",
                         (label, kind, key))
        db.commit()
        if cur.rowcount:
            print(f"named {key} -> {label} ({kind or 'no kind'})")
        else:
            print(f"unknown place {key} — run collect_place.sh there first")
        return

    rows = db.execute("""
        SELECT p.mac, p.label, p.kind, p.first_seen, p.last_seen,
               (SELECT COUNT(*) FROM location_samples s WHERE s.gateway_mac=p.mac)
        FROM places p ORDER BY p.last_seen DESC
    """).fetchall()
    if not rows:
        print("no places yet — run collect_place.sh first")
        return
    print(f"{'place key':<20} {'label':<12} {'kind':<8} {'samples':>7}  "
          f"first seen        last seen")
    for key, label, kind, first, last, n in rows:
        print(f"{key:<20} {label or '-':<12} {kind or '-':<8} {n:>7}  "
              f"{fmt(first)}  {fmt(last)}")


if __name__ == "__main__":
    main()
