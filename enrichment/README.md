# Match-report enrichment — Thai League 1 2025/2026 (season 15140)

One-pass job over the 240 completed TL1 2025/26 matches (`matches` posts 25500–25739).

## 1. Auto-created players

103 FootyStats `player_id`s appeared in the 240 matches' lineups/bench/cards but had
no WordPress `player` post (`footystats_id` meta). Almost all are the squads of the
three clubs relegated after 2025/26 — Nakhon Ratchasima, Muang Thong United,
Kanchanaburi Power — plus a few one-offs.

Each was fetched from FootyStats `/player-stats` and created as a `player` post
(`ewpa/create-cpt-item`, `status=publish`), posts **25771–25873**:

| field | source |
|---|---|
| `title`, `first_name`, `last_name` | FootyStats `known_as` / `first_name` / `last_name` |
| `nationality` | FootyStats `nationality`, verbatim country name |
| `position` | FootyStats position string → `GK` / `DF` / `MF` / `FW` (see mapping below) |
| `birthday` | FootyStats `birthday` (unix); omitted for 3 players FS has no DOB for |
| `last_confirmed_club` | WP `club` post ID of the side the player lined up for in their **latest** of the 240 matches — historical fact, **not** a current-squad claim |
| `last_confirmed_date` | that match's date, `YYYYMMDD` |
| `current_club` | **left unset** on all 103 |

### Position mapping (FootyStats string, lowercased → site value; first match wins)

1. empty → `""`
2. contains `keeper` → `GK`
3. contains `back` / `defender` / `defence` / `defense` / `cb` / `rb` / `lb` / `wb` → `DF`
4. contains `midfield` (or `dm`/`cm`/`am`) → `MF`
5. contains `forward` / `striker` / `winger` / `wing` / `attacker` / `cf` → `FW`
6. else → `""`

FootyStats emitted only: `Defender`, `Midfielder`, `Forward`, `Goalkeeper`, `Centre Back`.

## 2. Re-enrichment

With the lookup now complete (668 players), `player_stats` + `match_events` were
rebuilt for all 240 matches and `raw_match_data` (base64) re-written. Every
player row now resolves to a real name (0 numeric-ID fallbacks, was 1881 rows /
103 IDs).

## 3. xG

`team_a_xg → home_xg`, `team_b_xg → away_xg` (ACF fields, written via the REST
`acf` key). All 240 populated; 1 match (fs 8259336) is genuinely 0/0 in FootyStats.

Prematch xG: `team_a_xg_prematch → home_xg_prematch`, `team_b_xg_prematch →
away_xg_prematch` — plain post meta (not ACF), written via the connector
(`ewpa/update-cpt-item`), so NOT exposed to REST. All 240 written; 16 early-season
matches (matchweek 1, fs 7977061–7977071 range) are genuinely 0/0 — FootyStats
had no prematch model yet.

## Files

| file | description |
|---|---|
| `unmapped_player_ids.json` | the 103 FootyStats player IDs with no WP post |
| `new_players.json` | resolved name/position/nationality/birthday + computed last_confirmed_club/date |
| `create_player_payloads.json` | exact `ewpa/create-cpt-item` params for the 103 |
| `player_lookup.json` | `footystats_id → {wp_post_id, title}` for all 668 players post-run |
| `fs_team_to_wp_15140.json` | FootyStats team ID → WP `club` post ID (16 TL1 25/26 clubs) |
| `reenrich_payloads.json` | per-post `raw_match_data` + `home_xg`/`away_xg` for all 240 |
| `write_reenrich.py` | idempotent bulk writer (REST, resumable via `reenrich_done.json`) |
