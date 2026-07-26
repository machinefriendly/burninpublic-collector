-- SQLite schema. Raw MACs and per-request rows never leave this file.

-- The `gateway_mac` / `mac` columns hold a *place key*: either a normalized
-- gateway MAC (aa:bb:cc:dd:ee:ff) for a router bolted to a building, or
-- geo:<version>:<row>_<col> for a portable gateway such as a phone hotspot,
-- whose MAC travels with you (see place_key.py). The column names predate the
-- second form; renaming them would change nothing but risk breaking the salted
-- hashes already on the server, which are computed from these exact values.
CREATE TABLE IF NOT EXISTS location_samples (
    ts          INTEGER NOT NULL,           -- epoch seconds
    gateway_mac TEXT    NOT NULL,           -- place key
    ssid        TEXT,
    PRIMARY KEY (ts, gateway_mac)
);

CREATE TABLE IF NOT EXISTS places (
    mac          TEXT PRIMARY KEY,          -- place key = place fingerprint
    label        TEXT,                      -- human name (places.py, or the web)
    label_synced TEXT,                      -- label as the server last knew it
    kind         TEXT,                      -- e.g. home / office / cafe
    first_seen   INTEGER,
    last_seen    INTEGER
);

-- Last CoreLocation fix, for the portable-gateway path only. Single row.
-- Raw coordinates live here and are never uploaded; `places` gets the coarse
-- cell centre instead.
CREATE TABLE IF NOT EXISTS geo_state (
    id         INTEGER PRIMARY KEY CHECK (id = 1),
    ts         INTEGER NOT NULL,            -- epoch seconds of the fix
    lat        REAL,
    lon        REAL,
    accuracy_m REAL,
    cell       TEXT,                        -- current grid cell, "<row>_<col>"
    net_sig    TEXT                         -- gateway MAC|SSID when fixed
);

CREATE TABLE IF NOT EXISTS usage_requests (
    request_id            TEXT PRIMARY KEY, -- dedup key
    ts                    INTEGER NOT NULL, -- epoch seconds
    source                TEXT NOT NULL,    -- claude | codex
    project               TEXT,
    model                 TEXT,
    input_tokens          INTEGER DEFAULT 0,
    output_tokens         INTEGER DEFAULT 0,
    cache_read_tokens     INTEGER DEFAULT 0,
    cache_creation_tokens INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_usage_ts   ON usage_requests (ts);
CREATE INDEX IF NOT EXISTS idx_samples_ts ON location_samples (ts);

CREATE TABLE IF NOT EXISTS parse_state (
    path        TEXT PRIMARY KEY,
    byte_offset INTEGER NOT NULL DEFAULT 0,
    mtime       REAL    NOT NULL DEFAULT 0
);
