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

## API

- FootyStats: `https://api.football-data-api.com/` — endpoints `/league-list`, `/league-teams`, `/league-matches`
- WordPress: footballsiam connector, `ewpa/get-cpt-items`, `ewpa/create-cpt-item`, `ewpa/update-post-meta`, `ewpa/get-cpt-taxonomies`
