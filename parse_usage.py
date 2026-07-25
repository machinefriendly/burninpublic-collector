#!/usr/bin/env python3
"""Scan ~/.claude/projects/**/*.jsonl and ~/.codex/sessions/ for token usage.

Incremental (byte offsets in parse_state) + dedups by requestId.
"""
import glob
import json
import os
import sqlite3
from datetime import datetime, timezone

DB = os.environ.get("AIWORK_DB", os.path.expanduser("~/.aiwork/local.db"))
SCHEMA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "local_schema.sql")


def to_epoch(ts):
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        return int(ts / 1000) if ts > 1e12 else int(ts)
    try:
        return int(datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def rows_from_claude(line, path, lineno):
    rec = json.loads(line)
    usage = (rec.get("message") or {}).get("usage")
    if not usage:
        return None
    rid = rec.get("requestId") or (rec.get("message") or {}).get("id") \
        or f"{path}:{lineno}"
    ts = to_epoch(rec.get("timestamp"))
    if ts is None:
        return None
    project = os.path.basename(os.path.dirname(path))
    return (rid, ts, "claude", rec.get("cwd") or project,
            (rec.get("message") or {}).get("model"),
            usage.get("input_tokens", 0) or 0,
            usage.get("output_tokens", 0) or 0,
            usage.get("cache_read_input_tokens", 0) or 0,
            usage.get("cache_creation_input_tokens", 0) or 0)


def rows_from_codex(line, path, lineno):
    rec = json.loads(line)
    payload = rec.get("payload") or {}
    if payload.get("type") != "token_count":
        return None
    info = payload.get("info") or {}
    last = info.get("last_token_usage") or {}
    if not last:
        return None
    ts = to_epoch(rec.get("timestamp"))
    if ts is None:
        return None
    rid = f"codex:{os.path.basename(path)}:{lineno}"
    cached = last.get("cached_input_tokens", 0) or 0
    # OpenAI-style usage: input_tokens INCLUDES the cached portion.
    fresh = max(0, (last.get("input_tokens", 0) or 0) - cached)
    return (rid, ts, "codex", None, info.get("model"),
            fresh, last.get("output_tokens", 0) or 0, cached, 0)


def scan(db, pattern, extract):
    files = sorted(glob.glob(pattern, recursive=True))
    inserted = skipped = 0
    for path in files:
        try:
            mtime = os.path.getmtime(path)
            size = os.path.getsize(path)
        except OSError:
            continue
        row = db.execute("SELECT byte_offset, mtime FROM parse_state WHERE path=?",
                         (path,)).fetchone()
        offset = row[0] if row else 0
        if offset > size:            # file truncated/rewritten — rescan
            offset = 0
        if row and offset == size and mtime <= row[1]:
            continue
        with open(path, "rb") as fh:
            fh.seek(offset)
            data = fh.read()
        new_offset = offset + len(data)
        for lineno, raw in enumerate(data.decode("utf-8", "replace").splitlines()):
            if not raw.strip():
                continue
            try:
                parsed = extract(raw, path, lineno)
            except (json.JSONDecodeError, TypeError, AttributeError):
                continue
            if parsed is None:
                continue
            cur = db.execute(
                "INSERT OR IGNORE INTO usage_requests VALUES (?,?,?,?,?,?,?,?,?)",
                parsed)
            inserted += cur.rowcount
            skipped += 1 - cur.rowcount
        db.execute(
            "INSERT INTO parse_state (path, byte_offset, mtime) VALUES (?,?,?) "
            "ON CONFLICT(path) DO UPDATE SET byte_offset=excluded.byte_offset, "
            "mtime=excluded.mtime", (path, new_offset, mtime))
    return len(files), inserted, skipped


def main():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    db = sqlite3.connect(DB)
    with open(SCHEMA) as fh:
        db.executescript(fh.read())

    n1, i1, s1 = scan(db, os.path.expanduser("~/.claude/projects/**/*.jsonl"),
                      rows_from_claude)
    n2, i2, s2 = scan(db, os.path.expanduser("~/.codex/sessions/**/*.jsonl"),
                      rows_from_codex)
    db.commit()

    total = db.execute("SELECT COUNT(*), COALESCE(SUM(input_tokens+output_tokens"
                       "+cache_read_tokens+cache_creation_tokens),0) "
                       "FROM usage_requests").fetchone()
    print(f"claude: {n1} files, {i1} new rows ({s1} dupes)")
    print(f"codex : {n2} files, {i2} new rows ({s2} dupes)")
    print(f"db total: {total[0]} requests, {total[1]:,} tokens (all classes)")


if __name__ == "__main__":
    main()
