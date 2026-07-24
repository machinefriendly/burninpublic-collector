# mf-taktoken

Track AI coding token usage (Claude Code + Codex) and *where* it happened,
using the default-gateway MAC as a privacy-light place fingerprint.

| File | Purpose |
|---|---|
| `local_schema.sql` | SQLite schema. Raw MACs and per-request rows never leave this file. |
| `collect_place.sh` | Location sampler. Default-gateway MAC as the place fingerprint. Runs every 5 min. |
| `parse_usage.py` | Scans `~/.claude/projects/**/*.jsonl` and `~/.codex/sessions/`. Incremental + dedups by `requestId`. |
| `join_and_push.py` | As-of join, active-time rollup, salted-hash upload to Supabase. |
| `places.py` | List and name detected places. |
| `supabase_schema.sql` | Remote tables + the three metric views. |
| `launchd/com.sig.aiwork.collect.plist` | 5-minute location sampler agent. |
| `launchd/com.sig.aiwork.sync.plist` | Nightly parse + push agent (03:15). |

Local database lives at `~/.aiwork/local.db` (override with `AIWORK_DB`).

## Setup

```bash
cd /Users/lianglu/Documents/github/mf-taktoken
chmod +x collect_place.sh

./collect_place.sh                            # first sample + creates schema
python3 parse_usage.py                        # backfill all history
AIWORK_LOCAL_ONLY=1 python3 join_and_push.py  # inspect before uploading
python3 places.py                             # see detected places
python3 places.py aa:bb:cc:dd:ee:ff "home" home # name one
```

`AIWORK_LOCAL_ONLY=1` writes the joined report to
`reports/token_location_report.csv` instead of uploading.

Nothing leaves the machine until you drop `AIWORK_LOCAL_ONLY` and set
`SUPABASE_URL` / `SUPABASE_KEY`.

```bash
sed -i '' "s|REPLACE_ME|$PWD|g" launchd/*.plist
cp launchd/*.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.sig.aiwork.*.plist
```

Historical requests recorded before the first location sample join to
`unknown` — the as-of join only matches samples up to 30 min older than
the request.
