"""Owner-only SQLite access for the local telemetry DB.

The DB holds raw place keys, SSIDs, project paths, and exact GPS fixes, so
every entry point opens it through here rather than sqlite3.connect directly:
a default 022 umask would otherwise create the DB — and its -wal/-shm/-journal
side files — world-readable for other local accounts. chmod runs after connect
too, which also repairs files created 0644 by older releases.

No local imports on purpose: place_key, locate_places, and join_and_push
import each other in a chain, and this module must be importable by all of
them without joining the cycle.
"""
import os
import sqlite3

AIWORK_DIR = os.path.expanduser("~/.aiwork")


def connect(path):
    path = os.path.expanduser(path)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
        # Lock down the directory too, but only ours — a custom AIWORK_DB in
        # a directory the user shares deliberately is their call, not ours.
        if os.path.realpath(parent).startswith(os.path.realpath(AIWORK_DIR)):
            try:
                os.chmod(parent, 0o700)
            except OSError:
                pass
    prior = os.umask(0o077)   # files *born* 0600, no exposure window
    try:
        db = sqlite3.connect(path)
    finally:
        os.umask(prior)
    for side in ("", "-wal", "-shm", "-journal"):
        try:
            os.chmod(path + side, 0o600)
        except OSError:
            pass                # side file doesn't exist right now — fine
    return db
