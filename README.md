# footballsiam-data

Source data and generated payloads for the **2026/27 fixtures import** into the
[footballsiam.com](https://footballsiam.com) WordPress `matches` custom post type.

Populated on 2026-09-03 via the FootyStats API + the footballsiam WordPress connector
(`ewpa/*` abilities). This repo holds the raw inputs and the exact payloads that were
written, so the import is reproducible / auditable.

## What was imported

| Competition | FootyStats `season_id` | WP `competition` term | WP `category` term | Matches |
|---|---|---|---|---|
| Thai League 1 | 17367 (2026/27) | 116 | 204 (`Thai League 1 2026/27`) | 240 |
| Thai League 2 | 17896 (2026/27) | 117 | 209 (`Thai League 2 2026/27`) | 306 |

All 546 posts: `post_type=matches`, `post_status=publish`, `season` term `172` (`2026`).

### Per-post meta

| meta key | value |
|---|---|
| `home_team` / `away_team` | WP `club` post ID (via mapping below) |
| `home_score` / `away_score` | `""` (empty — all fixtures unplayed) |
| `date` | `YYYYMMDD` (8 digits, no separators) — **required by the site's `fs_get_matches()` regex** |
| `kick_off_time` | `H:i`, 24h, derived from FootyStats `date_unix` in **Asia/Bangkok** |
| `match_week` | FootyStats `game_week` (or `revised_game_week` when not `-1`) |
| `season` | literal string `"2026/2027"` |
| `footystats_match_id` | FootyStats match `id` — idempotency key |

> **Date-format note:** the first import pass wrote `date` as ISO `YYYY-MM-DD`, which
> failed the site's regex and silently fell back to `post_date` (publish date). All 292
> already-created posts were corrected to `YYYYMMDD`, and the remaining 254 were created
> in that format. See `date_fixes.json`.

## Club ID mapping (FootyStats team ID -> WordPress `club` post ID)

### Thai League 1
```
1180:7931  1182:7928  1184:7930  678763:7942  1171:7933  1172:7935
1173:7929  1177:7937  1186:7934  7038:7938    8374:7939  9887:7941
9898:7940  666944:7953 678796:7961 688240:7959
```

### Thai League 2
```
1187:7944   1189:7945   21948:7951  9886:7949   688585:7943 8372:7948
8384:7956   678797:7960 666943:13879 1174:7936  8381:7952   1188:7932
8371:7947   688234:7958 9889:7954   1023811:9862 21927:7955 666933:9792
```

## Files

| file | description |
|---|---|
| `lm_17367.json` | raw FootyStats `/league-matches` response — Thai League 1 (240 matches, full objects) |
| `lm_17896.json` | raw FootyStats `/league-matches` response — Thai League 2 (306 matches, full objects) |
| `payloads.json` | 543 generated WP create payloads (excludes the 3 dry-run fixtures), ISO dates as originally built |
| `resume_creates.json` | 254 create payloads used for the resume pass, `date` already in `YYYYMMDD` |
| `date_fixes.json` | 292 `{post_id, ymd}` corrections applied via `ewpa/update-post-meta` |

## Daily sync automation

`src/` holds the ongoing sync that keeps the `matches` CPT in step with FootyStats.

| file | role |
|---|---|
| `src/footystats_client.py` | thin FootyStats API wrapper; `get_league_matches(season_id)` |
| `src/wp_client.py` | WordPress REST client (Application Passwords / HTTP Basic) |
| `src/sync_matches.py` | orchestration + CLI |
| `.github/workflows/daily-sync.yml` | runs it on schedule |

### What the sync does per run

For TL1 (`17367`) and TL2 (`17896`), every FootyStats match is matched to the WP
post whose `footystats_match_id` meta equals its id:

- **no post** → create it (same field + club-ID mapping as the initial import;
  `date` as `YYYYMMDD`, `season` `"2026/2027"`, taxonomy terms as in the table above)
- **post exists, FootyStats status `complete`, WP `home_score` still empty** →
  write `home_score`, `away_score`, `match_week`
- **post already has a score** → left untouched, always
- **team id not in the mapping** → skipped and logged, never guessed

Ends with a one-line summary: checked / scores updated / created / skipped / errors.

### Running it

```bash
pip install -r requirements.txt
export FOOTYSTATS_API_KEY=...  WP_APP_USERNAME=...  WP_APP_PASSWORD=...

python src/sync_matches.py            # DRY RUN (reads only, no writes)
python src/sync_matches.py --check    # verify connectivity + credentials
python src/sync_matches.py --live     # actually write
python src/sync_matches.py --league "Thai League 2" --limit 5   # scoped test
```

### Schedule (GitHub Actions)

- `15 15 * * *` → 22:15 Asia/Bangkok
- `0 20 * * *` → 03:00 Asia/Bangkok
- `workflow_dispatch` for manual runs (with an optional `dry_run` toggle and league filter)

Secrets required on the repo: `FOOTYSTATS_API_KEY`, `WP_APP_USERNAME`, `WP_APP_PASSWORD`.

## WordPress REST prerequisites

The sync talks to the plain `wp-json` REST API, not the connector used for the
initial import. Two things must be true on the site:

1. **Application Password** for a user who can edit `matches` posts
   (Users → Profile → Application Passwords in wp-admin). Username = that user's
   `user_login`; password = the generated 24-char string (spaces are fine).

2. **Custom-field meta exposed to REST.** Each meta key the sync reads or writes —
   `home_score`, `away_score`, `date`, `kick_off_time`, `match_week`, `season`,
   `footystats_match_id`, `home_team`, `away_team` — must be registered with
   `show_in_rest => true` and a write-capable `auth_callback` on the `matches`
   post type. Without this, `meta` reads back empty and writes are silently dropped.

   **Install `wordpress/register-match-meta.php`** on the site (Code Snippets
   plugin / child-theme `functions.php` / mu-plugin — see the file header). It
   registers all 9 keys as `string`, which is how the data is already stored and
   how the theme's `fs_get_matches()` reads it.

   `sync_matches.py` has a hard guard: if it indexes 0 posts by
   `footystats_match_id` while the CPT is non-empty, it aborts instead of creating
   duplicates. A clean dry run once the snippet is active looks like
   `checked 546 / created 0 / scores updated 0 / skipped(not complete) 546`.

## Sync automation — Part A (match enrichment) and Part B (club reassignment)

### Part A — full enrichment on result

Folded into the twice-daily `sync_matches.py` run (`src/enrich.py`). When a
`matches` post is `complete` in FootyStats **and still has no `raw_match_data`**,
the sync fetches `/match` detail and writes, in one REST call:

- team stats — `home/away_` × `corners, offsides, on_target, off_target,
  attempts, fouls, possession` (`team_a_*` / `team_b_*`)
- `home_xg` / `away_xg` (ACF) and `home_xg_prematch` / `away_xg_prematch` (meta)
- `raw_match_data` — base64 JSON: `player_stats[]` with `wp_id` + `position`,
  `match_events[]` with `p1_wp_id`; own-goal detection cross-checks the goal
  minute against that team's own goal-timing array

Missing players are auto-created from `/player-stats` using the approved
position map (`GK/DF/MF/FW`), with `last_confirmed_club` + `last_confirmed_date`
and **no `current_club`**. The `footystats_id → WP` lookup is rebuilt fresh
every run and grown in-memory as players are created. Every write emits an
`AUDIT | …` log line. Idempotent: `raw_match_data` present ⇒ skip.

Disable with `--no-enrich`. Guard test: `--enrich-test <post_id,post_id>`.

### Part B — `current_club` reassignment (`--club-sync`, weekly)

`weekly-club-sync.yml` (Mondays). For every `player`, fetches the **live**
FootyStats `/player-stats` list (~565 calls) and finds the most recent row that
is:

- `league_type == "Domestic League"` — no cups, no internationals (this also
  drops AFC Champions League / President's Cup / AFC U23, whose `club_team_id`
  is a select or national side, not a club)
- `club_team_id > 0`
- `appearances_overall > 0` — a roster listing with no minutes is not evidence
- `ending_year >= (this year − 1)` — a 2-3-season-old appearance is not evidence
  of a player's *current* club

ordered by `(starting_year, ending_year, last_match_timestamp)`. That row's
`club_team_id` is then **ID-matched** against the 34-club `CLUB_MAP`:

- tracked club, differs from `current_club` → set `current_club`
- not a tracked club, `current_club` set → clear it
- already correct → no change
- **no eligible row** → no action, logged `NO-EVIDENCE`

Never name-matches. The derived blobs in `fs_advanced_stats_json`
(`all_seasons_metrics`, `career_history_rich`) are **not** used — the first
collapses competitions per season and silently drops transfers.

Runs report-only; writes only when dispatched with `live=true`. Set
`CLUB_SYNC_DECISION_DUMP=<path>` to also emit one JSON line per player for
offline analysis.

> **Status: reviewed, not yet live.** See the session report for the dry-run
> breakdown and the two open calls (whether to write `current_club` on the
> Part-A auto-created players, and the residual thin `apps=1` reassigns).

### New WordPress prerequisites for Part A / B

Two more snippets alongside `register-match-meta.php`:

- **`wordpress/register-match-stats-meta.php`** — now also registers
  `home_xg_prematch`, `away_xg_prematch`. Re-deploy the updated file.
- **`wordpress/register-player-meta.php`** (new) — registers
  `last_confirmed_club`, `last_confirmed_date`, `birthday` on `player` so
  auto-created players keep those values over REST.

`home_xg` / `away_xg` and every `player` ACF field (`footystats_id`,
`first_name`, `last_name`, `nationality`, `position`, `current_club`) are
written through the REST `acf` object and need no snippet.

## API reference

- FootyStats: `https://api.football-data-api.com/` — `/league-list`, `/league-teams`, `/league-matches`, `/match`, `/player-stats`
- WordPress (sync): `https://footballsiam.com/wp-json/wp/v2/` — `match`, `club`, `player`, `users/me`
- WordPress (initial import): footballsiam connector — `ewpa/get-cpt-items`, `ewpa/create-cpt-item`, `ewpa/update-post-meta`, `ewpa/get-cpt-taxonomies`
