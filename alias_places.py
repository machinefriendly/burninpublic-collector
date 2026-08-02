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
- Recomputed on every run from the same ordering, so the result is stable;
  naming a previously aliased place (places.py or the web) promotes it back
  to canonical on the next run.
"""
import math

MERGE_RADIUS_M = 200


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
    db.execute("UPDATE places SET alias_of = NULL")
    for a, c in alias.items():
        db.execute("UPDATE places SET alias_of = ? WHERE mac = ?", (c, a))
    db.commit()
    return alias
