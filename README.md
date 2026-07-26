# burninpublic-collector

The open-source collector for [burninpublic.com](https://burninpublic.com) —
see where your AI-coding tokens burn. It tracks Claude Code + Codex token
usage on your Mac, joins it to the *places* you work from, and syncs daily
totals to your private dashboard: map, trends, share cards.

## How the system works

Two parts:

1. **This collector** (open source, runs on your machine) — samples a
   privacy-light place fingerprint every 5 minutes, parses your local
   Claude Code / Codex usage logs, and uploads **aggregates only** —
   hourly totals, no individual requests — once a night (03:15).
2. **The web app** ([burninpublic.com](https://burninpublic.com)) — your
   dashboard. Sign in with magic link, Google, or GitHub; you see only
   your own data.

## Privacy model — what leaves your machine

The collector is open source precisely so you can verify this table
against the code (`join_and_push.py` is the only file that uploads).

| Stays on your machine (never uploaded) | Uploaded to your account |
|---|---|
| Raw gateway MAC addresses (the place fingerprint) | A salted hash of each fingerprint — irreversible without the salt file that never leaves `~/.aiwork/salt` |
| Per-request usage rows and timestamps | Hourly totals: tokens by UTC hour × place × source × model, plus active minutes |
| Project names, file paths | — |
| Your prompts and code — **never even read**; only token *counts* are parsed from the logs | — |
| Continuous location samples, and every exact GPS fix | Only the name + coordinates of places **you explicitly named** with `places.py` — an auto-detected place you never named is never uploaded, not even its coordinates |

Two details worth knowing about that table:

- **Why hours, not days.** Buckets are UTC hours so your dashboard can show
  days in *your* timezone — a day-level total can't be re-cut across another
  timezone's midnight, and your Mac's timezone isn't necessarily where you are.
  An hour bucket says "some requests happened in this hour", never when inside
  it or what they were.
- **Coordinates are coarse for hotspot places.** When your gateway is a phone
  hotspot, the fingerprint is a ~250 m grid cell (see
  [`place_key.py`](place_key.py)) and the stored coordinates are the cell's
  centre, not your exact fix. If you then name that place and it syncs, it
  reveals the cell, not the desk.

Every uploaded row is bound to your user id and protected by Postgres
row-level security: no anonymous access, and no account can query another
account's rows. See [`supabase_schema.sql`](supabase_schema.sql) for the
exact policies. Don't want anything uploaded at all? Use
[local-only mode](#local-only-mode) — full reports, nothing leaves the Mac.

## Install (no clone needed)

```bash
curl -fsSL https://raw.githubusercontent.com/machinefriendly/burninpublic-collector/main/install.sh | bash
```

Prefer to read everything first? Same result:

```bash
git clone https://github.com/machinefriendly/burninpublic-collector.git
cd burninpublic-collector && ./install.sh
```

The installer copies the scripts to `~/.aiwork/bin` and loads two launchd
agents: a 5-minute place sampler and the nightly 03:15 sync. No root, no
sudo, everything under your own user.

## Permissions it will ask for — and why

- **Location Services** (one macOS prompt, *optional*): the bundled
  `AiworkLocate.app` resolves your named places to map coordinates, once
  per place. Deny it and everything else still works — your dashboard
  simply shows places without a real map position.
- **Background agents** (launchd): the 5-minute sampler and nightly sync.
  Visible via `launchctl list | grep aiwork`; uninstall removes them.
- **Read access to local usage logs**: `~/.claude/projects/**/*.jsonl`
  and `~/.codex/sessions/` — read-only, parsed locally for token counts.
- Nothing else: no root, no Full Disk Access, no browser access, no
  network traffic except the nightly upload to your own account.

## Connect your account

```bash
python3 ~/.aiwork/bin/login.py     # enter email → one-time code from your inbox
```

Passwordless, once per machine: the same one-time email code the web app
uses, so it works no matter how you signed up — magic link, Google, or
GitHub (identities are linked by verified email). It stores a refresh
token in `~/.aiwork/session.json` (mode 600) — no admin keys ever touch
your machine, and uploads run as *you* under row-level security.

## Name your places

```bash
python3 ~/.aiwork/bin/places.py                          # list detected places
python3 ~/.aiwork/bin/places.py aa:bb:cc:dd:ee:ff "wework" work   # name one
```

Naming a place is what opts it into geolocation + map display.

## Local-only mode

```bash
AIWORK_LOCAL_ONLY=1 python3 ~/.aiwork/bin/join_and_push.py
```

Writes the full joined report to `reports/token_location_report.csv`
instead of uploading — inspect exactly what *would* be sent, or just use
the collector as a fully offline tracker.

## Self-hosting

Don't want to use the hosted backend? Create your own
[Supabase](https://supabase.com) project, apply
[`supabase_schema.sql`](supabase_schema.sql), and point the collector at
it via `~/.aiwork/supabase.env`:

```
SUPABASE_URL=https://YOUR-PROJECT.supabase.co
SUPABASE_ANON_KEY=YOUR-ANON-KEY
```

## Uninstall

```bash
launchctl unload ~/Library/LaunchAgents/com.sig.aiwork.*.plist
rm -f ~/Library/LaunchAgents/com.sig.aiwork.*.plist
rm -rf ~/.aiwork        # includes the local database — export first if you care
```

## Files

| File | Purpose |
|---|---|
| `collect_place.sh` | 5-min place sampler (default-gateway MAC as fingerprint). |
| `parse_usage.py` | Parses Claude Code / Codex logs locally. Incremental, dedups by request id. |
| `join_and_push.py` | As-of join, daily rollup, salted-hash upload. The only file that uploads. |
| `login.py` | One-time account login (stores a refresh token, 0600). |
| `places.py` | List and name detected places. |
| `locate_places.py` | Resolves named places to coordinates (CoreLocation + OpenStreetMap). |
| `local_schema.sql` | Local SQLite schema (`~/.aiwork/local.db`). |
| `supabase_schema.sql` | Server schema snapshot, for auditing RLS or self-hosting. |
| `install.sh` / `launchd/` | Installer + the two launchd agents. |

MIT licensed. Historical note: requests recorded before your first location
sample join to `unknown` — the as-of join only matches samples up to 30
minutes older than a request.
