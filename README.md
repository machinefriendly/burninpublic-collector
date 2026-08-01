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
   hourly totals, no individual requests — every 15 minutes (just the
   last two days), plus a full reconciliation pass nightly at 03:15.
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
| Continuous location samples, and every exact GPS fix | Which **~250 m cell** each detected place sits in, plus its neighbourhood name. Places **you named** upload their exact coordinates instead — naming is consent |
| Your Claude Code / Codex history from *before* you installed the collector — parsed locally for your own reports, never uploaded | — |

One third-party service is involved: OpenStreetMap's Nominatim, which turns
coordinates into a place *name* (reverse geocoding). This happens
**automatically, once per newly detected place** — that is how a place can
appear on your map with a neighbourhood name before you have named it. What
Nominatim receives is the **centre of the ~250 m cell**, never the exact fix
(which stays in the local database), and never your account, usage, or place
fingerprints. The stored short name is capped at neighbourhood resolution;
street-level detail stays in a local-only column.

Four details worth knowing about that table:

- **Why hours, not days.** Buckets are UTC hours so your dashboard can show
  days in *your* timezone — a day-level total can't be re-cut across another
  timezone's midnight, and your Mac's timezone isn't necessarily where you are.
  An hour bucket says "some requests happened in this hour", never when inside
  it or what they were.
- **Why unnamed places are uploaded at all.** So they appear on your map, where
  you can name them — otherwise naming would only be possible in the terminal,
  and a place you haven't named yet would be invisible. What that costs you is
  bounded and stated: which 250 m cell, never where in it. Naming a place then
  upgrades it to its exact fix, because at that point you have chosen to.
- **Why history stops at install time.** The log files go back months, but there
  were no location samples then, so none of it can be attributed to a place.
  Uploading it anyway would put a headline total on your dashboard that the
  place list underneath could never add up to. So the upload starts at your
  first location sample, and everything on the dashboard reconciles: places plus
  unmapped equals the total. Your full history is still in `~/.aiwork/local.db`
  and still shows up in local reports.
- **Want the stricter rule?** `AIWORK_HIDE_UNNAMED=1` keeps unnamed places off
  the server entirely — a place then only exists remotely once you name it with
  `places.py`. Nothing else changes.

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

The installer copies the scripts to `~/.aiwork/bin`, loads the launchd
agents (a 5-minute place sampler, a 15-minute incremental sync, and the
nightly 03:15 full sync), then prompts you to sign in — same one-time
email code the web app uses. No root, no sudo, everything under your own
user. Skip the sign-in with Ctrl-C and run it later with
`python3 ~/.aiwork/bin/login.py`.

## Permissions it will ask for — and why

- **Location Services** (one macOS prompt, *optional*): the bundled
  `AiworkLocate.app` resolves your named places to map coordinates, once
  per place. Deny it and everything else still works — your dashboard
  simply shows places without a real map position.
- **Background agents** (launchd): the 5-minute sampler, the 15-minute
  incremental sync, and the nightly full sync.
  Visible via `launchctl list | grep aiwork`; uninstall removes them.
- **Read access to local usage logs**: `~/.claude/projects/**/*.jsonl`
  and `~/.codex/sessions/` — read-only, parsed locally for token counts.
- Nothing else: no root, no Full Disk Access, no browser access, no
  network traffic except the aggregate uploads to your own account.

## Connect your account

The installer does this at the end. To redo it (or if you skipped it):

```bash
python3 ~/.aiwork/bin/login.py     # enter email → one-time code from your inbox
```

Passwordless, once per machine: the same one-time email code the web app
uses, so it works no matter how you signed up — magic link, Google, or
GitHub (identities are linked by verified email). It stores a refresh
token in `~/.aiwork/session.json` (mode 600) — no admin keys ever touch
your machine, and uploads run as *you* under row-level security.

## Name your places

Easiest on [the dashboard](https://burninpublic.com): click a place's name
in the list and type — the collector picks the label up on its next sync.
The same works from the terminal:

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
