-- SQLite schema. Raw MACs and per-request rows never leave this file.

CREATE TABLE IF NOT EXISTS location_samples (
    ts          INTEGER NOT NULL,           -- epoch seconds
    gateway_mac TEXT    NOT NULL,           -- normalized aa:bb:cc:dd:ee:ff
    ssid        TEXT,
    PRIMARY KEY (ts, gateway_mac)
);

CREATE TABLE IF NOT EXISTS places (
    mac        TEXT PRIMARY KEY,            -- gateway MAC = place fingerprint
    label      TEXT,                        -- human name, set via places.py
    kind       TEXT,                        -- e.g. home / office / cafe
    first_seen INTEGER,
    last_seen  INTEGER
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
