#!/usr/bin/env python3
"""Log the collector into your TakToken account (once per machine).

    python3 taktoken_login.py                     # prompts
    python3 taktoken_login.py EMAIL PASSWORD      # non-interactive

Stores a refresh token in ~/.aiwork/session.json (0600). Uploads then run as
YOUR user under row-level security — no admin keys on this machine.
"""
import getpass
import json
import os
import ssl
import sys
import urllib.request

ENV_FILE = os.path.expanduser("~/.aiwork/supabase.env")
SESSION_FILE = os.path.expanduser("~/.aiwork/session.json")

try:
    import certifi
    SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    SSL_CTX = ssl.create_default_context()


def load_env():
    env = {}
    with open(ENV_FILE) as fh:
        for line in fh:
            if "=" in line and not line.startswith("#"):
                k, _, v = line.strip().partition("=")
                env[k] = v
    return env


def main():
    env = load_env()
    url, anon = env["SUPABASE_URL"], env["SUPABASE_ANON_KEY"]
    email = sys.argv[1] if len(sys.argv) > 2 else input("email: ")
    password = sys.argv[2] if len(sys.argv) > 2 else getpass.getpass("password: ")

    req = urllib.request.Request(
        f"{url}/auth/v1/token?grant_type=password",
        data=json.dumps({"email": email, "password": password}).encode(),
        headers={"apikey": anon, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, context=SSL_CTX) as resp:
        data = json.load(resp)

    with open(SESSION_FILE, "w") as fh:
        json.dump({"user_id": data["user"]["id"],
                   "refresh_token": data["refresh_token"]}, fh)
    os.chmod(SESSION_FILE, 0o600)
    print(f"logged in as {email} ({data['user']['id']})")


if __name__ == "__main__":
    main()
