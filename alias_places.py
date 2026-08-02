"""Merge fingerprints that are the same physical place.

A place is identified by a gateway MAC (real router) or a ~250 m grid cell
(portable gateway), so one desk can produce several fingerprints: the Wi-Fi
router, the hotspot's grid cell, an adjacent cell from fix noise. To the
user that is ONE place, so located places within MERGE_RADIUS_M collapse
into a canonical one and the rest become aliases. Uploads and reports key
everything by the canonical place; aliases never leave the machine.

Rules, in order:
- A place the user NAMED is always its own canonical — naming is explicit
  intent, and two named places stay separate no matter how close.
- Unnamed places within the radius of a canonical alias to it; preference
  order is named first, then most location samples, then oldest.
- An unnamed fingerprint with NO coordinates (a hotspot MAC recorded while
  no location fix was available) can't distance-merge, but if its entire
  sample history is sandwiched in time by one located place — the sample
  just before it and the sample just after it both belong to that place,
  each within SANDWICH_GAP_S, with no other place interleaved — the
  machine never moved, so it aliases to that place too.
- Recomputed on every run from the same ordering, so the result is stable;
  naming a previously aliased place (places.py or the web) promotes it back
  to canonical on the next run.
"""
import math

MERGE_RADIUS_M = 200
# Neighbour sample must be this close in time. Sampling is every 5 minutes,
# so 15 min tolerates two missed beats while keeping the window a round trip
# to somewhere else would have to fit through as small as the evidence allows.
SANDWICH_GAP_S = 15 * 60


def _dist_m(lat1, lon1, lat2, lon2):
    """Equirectangular approximation — fine at city scale."""
    kx = 111_320 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot((lat1 - lat2) * 111_320, (lon1 - lon2) * kx)


def _is_named(label):
    return bool(label and label.strip())


def ensure_alias_column(db):
    have = {r[1] for r in db.execute("PRAGMA table_info(places)")}
    if "alias_of" not in have:
        db.execute("ALTER TABLE places ADD COLUMN alias_of TEXT")
        db.commit()


def refresh_aliases(db):
    """Recompute places.alias_of. Returns {alias_key: canonical_key}."""
    ensure_alias_column(db)
    have = {r[1] for r in db.execute("PRAGMA table_info(places)")}
    if "lat" not in have:            # nothing located yet — nothing to merge
        return {}
    samples = dict(db.execute(
        "SELECT gateway_mac, COUNT(*) FROM location_samples GROUP BY 1"))
    rows = db.execute("SELECT mac, label, lat, lon, first_seen FROM places "
                      "WHERE lat IS NOT NULL AND lon IS NOT NULL").fetchall()
    # Strongest first: named, then most samples, then oldest. Deterministic,
    # so reruns produce the same canonicals and aliases never flap.
    rows.sort(key=lambda r: (not _is_named(r[1]),
                             -samples.get(r[0], 0), r[4] or 0, r[0]))
    canonicals, alias = [], {}
    for key, label, lat, lon, _seen in rows:
        near = next((c for c in canonicals
                     if _dist_m(lat, lon, c[1], c[2]) <= MERGE_RADIUS_M), None)
        if near is not None and not _is_named(label):
            alias[key] = near[0]
        else:
            canonicals.append((key, lat, lon))
    _sandwich_pass(db, alias, {c[0] for c in canonicals})
    db.execute("UPDATE places SET alias_of = NULL")
    for a, c in alias.items():
        db.execute("UPDATE places SET alias_of = ? WHERE mac = ?", (c, a))
    db.commit()
    return alias


def _sandwich_pass(db, alias, located_canonicals):
    """Time-sandwich merge for unnamed fingerprints without coordinates.

    Evidence required, all of it: the place has samples; the nearest sample
    before its first and after its last both exist within SANDWICH_GAP_S;
    both resolve (through the distance aliases) to the same located
    canonical; and nothing belonging to any OTHER place interleaves inside
    the span. Sample history is immutable, so the verdict is stable across
    reruns — except that an open-ended stretch (still on that hotspot now)
    has no trailing neighbour yet and simply merges on a later run."""
    resolve = lambda k: alias.get(k, k)
    unlocated = db.execute(
        "SELECT mac, label FROM places WHERE lat IS NULL OR lon IS NULL "
        "ORDER BY mac").fetchall()
    for mac, label in unlocated:
        if _is_named(label) or mac in alias:
            continue
        span = db.execute(
            "SELECT MIN(ts), MAX(ts) FROM location_samples "
            "WHERE gateway_mac = ?", (mac,)).fetchone()
        if not span or span[0] is None:
            continue
        lo, hi = span
        before = db.execute(
            "SELECT gateway_mac, ts FROM location_samples "
            "WHERE ts < ? AND gateway_mac != ? ORDER BY ts DESC LIMIT 1",
            (lo, mac)).fetchone()
        after = db.execute(
            "SELECT gateway_mac, ts FROM location_samples "
            "WHERE ts > ? AND gateway_mac != ? ORDER BY ts ASC LIMIT 1",
            (hi, mac)).fetchone()
        if not before or not after:
            continue
        if lo - before[1] > SANDWICH_GAP_S or after[1] - hi > SANDWICH_GAP_S:
            continue
        canon = resolve(before[0])
        if canon != resolve(after[0]) or canon not in located_canonicals:
            continue
        inside = [r[0] for r in db.execute(
            "SELECT DISTINCT gateway_mac FROM location_samples "
            "WHERE ts BETWEEN ? AND ?", (lo, hi))]
        if any(k != mac and resolve(k) != canon for k in inside):
            continue
        alias[mac] = canon
