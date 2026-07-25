#!/usr/bin/env python3
"""Log the collector into your BurnInPublic account (once per machine).

    python3 login.py                     # email -> one-time code from your inbox
    python3 login.py EMAIL PASSWORD      # password fallback, non-interactive

Passwordless by default: the same one-time email code as the web app, so
Google / GitHub / magic-link accounts all work (Supabase links identities
by verified email). Stores a refresh token in ~/.aiwork/session.json
(0600). Uploads then run as YOUR user under row-level security — no admin
keys on this machine.
"""
import json
import os
import ssl
import sys
import urllib.error
import urllib.request

ENV_FILE = os.path.expanduser("~/.aiwork/supabase.env")
SESSION_FILE = os.path.expanduser("~/.aiwork/session.json")

try:
    import certifi
    SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    SSL_CTX = ssl.create_default_context()


# Hosted burninpublic.com backend. The anon key is a public, publishable
# value (it ships in the web app's JS too) — the real protection is
# row-level security on the server. Self-hosters override both via
# ~/.aiwork/supabase.env.
DEFAULT_URL = "https://fuicenrcljloczyvkqsg.supabase.co"
DEFAULT_ANON = ("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIs"
                "InJlZiI6ImZ1aWNlbnJjbGpsb2N6eXZrcXNnIiwicm9sZSI6ImFub24iLCJp"
                "YXQiOjE3ODQ5MTc2MzgsImV4cCI6MjEwMDQ5MzYzOH0."
                "D3WTP_WTiRQVSMEXrM_LI3Gf_nND_45WBboakrPZEYw")


def load_env():
    env = {"SUPABASE_URL": DEFAULT_URL, "SUPABASE_ANON_KEY": DEFAULT_ANON}
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE) as fh:
            for line in fh:
                if "=" in line and not line.startswith("#"):
                    k, _, v = line.strip().partition("=")
                    env[k] = v
    return env


def post(url, payload, anon):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"apikey": anon, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, context=SSL_CTX) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        raise SystemExit(f"auth failed ({e.code}): {detail}")


def save_session(data):
    with open(SESSION_FILE, "w") as fh:
        json.dump({"user_id": data["user"]["id"],
                   "refresh_token": data["refresh_token"]}, fh)
    os.chmod(SESSION_FILE, 0o600)
    print(f"logged in as {data['user']['email']} ({data['user']['id']})")


def main():
    env = load_env()
    url, anon = env["SUPABASE_URL"], env["SUPABASE_ANON_KEY"]

    if len(sys.argv) > 2:                       # password fallback
        data = post(f"{url}/auth/v1/token?grant_type=password",
                    {"email": sys.argv[1], "password": sys.argv[2]}, anon)
        return save_session(data)

    email = sys.argv[1] if len(sys.argv) > 1 else input("email: ")
    post(f"{url}/auth/v1/otp",
         {"email": email, "create_user": True}, anon)
    print(f"sent a one-time code to {email} — check your inbox")
    code = input("code: ").strip()
    data = post(f"{url}/auth/v1/verify",
                {"type": "email", "email": email, "token": code}, anon)
    save_session(data)


if __name__ == "__main__":
    main()
