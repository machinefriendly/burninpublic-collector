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
against the code. `join_and_push.py` is the only file that uploads your
usage and place data; the only other network calls are sign-in
(`login.py`) and the coarse Nominatim name lookup (`locate_places.py`).

| Stays on your machine (never uploaded) | Uploaded to your account |
|---|---|
| Raw gateway MAC addresses (the place fingerprint) | A salted hash of each fingerprint — irreversible without the salt file that never leaves `~/.aiwork/salt` |
| Per-request usage rows and timestamps | Hourly totals: tokens by UTC hour × place × source × model, plus active minutes (full field list below) |
| Project names, file paths | — |
| Your prompts and code — the parser decodes each log line in memory only to pull out the token-count fields; **nothing of their content is stored or uploaded** | — |
| Continuous location samples, and exact GPS fixes for every place **you have not named** | Which **~250 m cell** each detected place sits in, plus its neighbourhood name. Places **you named** upload their exact coordinates instead — naming is consent |
| Your Claude Code / Codex history from *before* you installed the collector — parsed locally for your own reports, never uploaded | — |
| The exact minute each place was last sampled (recorded locally every 5 minutes) | **`last_seen`**, rounded **down** to a 15-minute bucket, for every place that is uploaded at all — **including places you have not named**. It is re-sent every 15 minutes, so this is an ongoing "which place am I at now" signal, not a value written once. It marks the current place on the dashboard and separates a collector still running from one that stopped months ago (usage rows prove ownership, not recency). It adds no coordinate and changes no coordinate's precision. `AIWORK_HIDE_UNNAMED=1` removes it along with the unnamed places themselves |

### Every field that is uploaded

The table above is the summary; this is the whole list. Both payloads are
built in `join_and_push.py` and nowhere else — search it for `payload` and you
will find exactly these two.

**Usage rows** (`aiwork_daily`), one per UTC hour × place × source × model:

- `user_id` — your account id, the same one your login returns
- `day`, `hour` — UTC date and hour of the work
- `place_hash` — salted hash, or `null` for usage with no place attached
- `source` (`claude` / `codex`), `model` — the model name as the log reports it
- `requests`, `input_tokens`, `output_tokens`, `cache_read_tokens`,
  `cache_creation_tokens` — counts only; the four token classes are separate
  because they bill differently
- `active_minutes` — how many distinct minutes in that hour had any activity
- `uploaded_at` — the exact second **this upload ran**. This is finer than
  hourly, so it is called out: it records when the collector synced, not when
  you worked, and exists so a run can retire rows it has superseded

**Place rows** (`aiwork_places`), one per place:

- `user_id`, `place_hash` — as above
- `label` — the name you gave the place, empty string if you never named it
- `kind` — an optional free-text category you can type yourself when naming a
  place (`places.py KEY label kind`), e.g. `home` or `cafe`. Nothing sets it
  automatically; it is null until you write it. Being free text, whatever you
  put there is uploaded verbatim, so the "no project names" line below means
  the collector never *derives* one — it cannot stop you typing one in here
- `last_seen` — see the table above
- `lat`, `lon`, `geo_name` — only once a place has been located: the ~250 m
  cell centre for unnamed places, the exact fix for named ones

Nothing else. No prompts, no code, no project names, no file paths, no SSIDs,
no raw MAC addresses.

One third-party service is involved: OpenStreetMap's Nominatim, which turns
coordinates into a place *name* (reverse geocoding). This happens
**automatically, once per newly detected place** — that is how a place can
appear on your map with a neighbourhood name before you have named it. What
Nominatim receives is the **centre of the ~250 m cell**, never the exact fix
(which stays in the local database), and never your account, usage, or place
fingerprints. In [local-only mode](#local-only-mode) this lookup is skipped
entirely. The stored short name is capped at neighbourhood resolution;
street-level detail stays in a local-only column.

Five details worth knowing about that table:

- **One physical place is one place.** The same desk can produce several
  fingerprints — your Wi-Fi router, your phone hotspot's grid cell, an
  adjacent cell from GPS noise. Located fingerprints within ~200 m merge
  into one place before anything uploads (places you named are never merged
  away — naming is explicit intent). A hotspot fingerprint recorded while no
  location fix was available has no coordinates to merge by; it merges anyway
  when its entire sample history sits inside one located place's timeline
  (the nearest samples before and after both belong to that place, each
  within 15 minutes, with nothing else in between). That is strong evidence,
  not proof — an excursion that fits entirely inside those gaps would be
  misattributed. Merged usage counts under the surviving place, which may be
  one you named (so its exact coordinates apply); under
  `AIWORK_HIDE_UNNAMED=1` an unnamed fingerprint never rides along — its
  usage folds into Unmapped regardless of any merge.

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
  the server entirely: no metadata, and their hourly usage folds into the
  Unmapped row instead of being keyed by a place hash (totals stay correct).
  The next push also removes any unnamed-place metadata uploaded before the
  flag was set. A place then only exists remotely once you name it.
  **To make it stick, add the line `AIWORK_HIDE_UNNAMED=1` to
  `~/.aiwork/supabase.env`** — the background agents read that file on every
  run. Setting the variable on a one-off terminal command affects only that
  command, not the scheduled uploads.

Every uploaded row is bound to your user id and protected by Postgres
row-level security: no anonymous access, and no account can query another
account's rows. See [`supabase_schema.sql`](supabase_schema.sql) for the
exact policies. Don't want anything uploaded at all? Use
[local-only mode](#local-only-mode) — full reports, nothing leaves the Mac.

## Install (no clone needed)

```bash
curl -fsSL https://raw.githubusercontent.com/machinefriendly/burninpublic-collector/main/install.sh | bash
```

Note the one-liner runs whatever is on `main` *right now* — it is not pinned
to a reviewed release. If that matters to you, clone, review, and run
`./install.sh` from the checkout instead.

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
  `AiworkLocate.app` takes one fix per newly detected place — automatically,
  so places can appear on your map before you name them (at ~250 m grid
  accuracy; see the privacy table). Deny it and everything else still
  works — your dashboard simply shows places without a real map position.
- **Background agents** (launchd): the 5-minute sampler, the 15-minute
  incremental sync, and the nightly full sync.
  Visible via `launchctl list | grep aiwork`; uninstall removes them.
- **Read access to local usage logs**: `~/.claude/projects/**/*.jsonl`
  and `~/.codex/sessions/` — read-only, parsed locally for token counts.
- Nothing else: no root, no Full Disk Access, no browser access. Network
  traffic is exactly: the aggregate uploads + sign-in/refresh to your own
  account, the coarse (~250 m) name lookup to OpenStreetMap's Nominatim,
  and the install-time downloads from GitHub. (As with any HTTPS request,
  each of those hosts necessarily sees your public IP.)

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

Naming a place upgrades it from the ~250 m grid cell to its exact
coordinates on the map — unnamed places appear too, just coarsely (see the
privacy table).

One label is reserved: **"In transit"** (any capitalisation). Use it for
stops that are really a moving Mac — hotspot cells along a commute. The
dashboard groups every place with that label into a single row, and unlike
other names it is *not* location consent: an In-transit place keeps
grid-coarse (~250 m) coordinates, exactly like an unnamed one.

## Local-only mode

```bash
AIWORK_LOCAL_ONLY=1 python3 ~/.aiwork/bin/join_and_push.py
```

Writes the full joined report to `reports/token_location_report.csv`
instead of uploading — inspect exactly what *would* be sent, or just use
the collector as a fully offline tracker.

To stay offline permanently, add `AIWORK_LOCAL_ONLY=1` to
`~/.aiwork/supabase.env` — the scheduled agents read that file, so the
flag then applies to every run, not just commands you type it in front of.
With the flag in that file the Nominatim name lookup is skipped too, so
nothing leaves the machine at all — newly detected places simply appear
without a neighbourhood name.

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
| `install.sh` / `launchd/` | Installer + the three launchd agents. |

MIT licensed. Historical note: requests recorded before your first location
sample join to `unknown` — the as-of join only matches samples up to 30
minutes older than a request.
