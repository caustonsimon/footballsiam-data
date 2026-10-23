# ASEAN top flights build (24 Oct 2026)

Simon, 23 Oct 2026: add Indonesia, Malaysia, Singapore and Vietnam top flights:
fixtures, results, tables, clubs and players. English names only (no Thai);
four country headings in the Clubs menu / header dropdown after the T3 regions.

| League | FootyStats 2026/27 | Matches / teams | FotMob | Clubs |
|---|---|---|---|---|
| Indonesia Liga 1 (Super League) | 17539 | 297 / 18 | 8983 | 18 |
| Malaysia Super League | 17400 | 126 / 13 | 8985 | 12 |
| Singapore Premier League | 17437 | 84 / 8 | 461 | 8 |
| Vietnam V.League 1 | 17693 | 91 / 14 | 9088 | 14 |

Malaysia: FootyStats 13 teams v FotMob 12 - check for a withdrawn club.
FotMob depth (Indonesia sample): squads, line-ups, minutes, goals, assists; no
ratings / xG / team stats. Cups for later: FS Malaysia Cup 16611, MY FA Cup
17846, Singapore Cup 16399, IDN President's Cup 17077.

Order: matches sync (LEAGUES) -> 52 club pages + crests -> FotMob squads ->
page selectors + Clubs menu groups -> dry run, then live.

## Status, 24 Oct 2026 (06:30 Bangkok)

Done:
- Competition terms 254-257, category terms 258-261, club `league` terms 262-265.
- 52 club pages, posts 41224-41275, FotMob crests, `fotmob_team_id` meta. List and
  FootyStats/FotMob ids in `src/data/asean_clubs.json`. Pairing by fixture dates: every
  club 1:1; FootyStats 137 "Kelantan" is a stale duplicate id of Kelantan Red Warrior.
- Match sync: all four leagues in `LEAGUES` (598 fixtures/results created). Enriched:
  Singapore, Indonesia, Malaysia. V.League 1 enrichment still to run.
- Clubs menu (snippet 63): Indonesia / Malaysia / Singapore / Vietnam headings.
- Snippet 204 (`wordpress/asean-leagues.php`): country groups in the Fixtures/Results
  filter, four 2026/27 tables on the Tables page (orders checked against FotMob).
- Snippets 196/197: hub pages switch on for ASEAN clubs; source-aware footers.
- `fotmob_squads.py --club asean|indonesia|malaysia|singapore|vietnam`; FootyStats
  fallback for the 15 clubs FotMob has no squad for (9 Malaysian, 6 Singaporean).
  Daily schedule runs ASEAN after the Thai clubs.

Blocked: GitHub Actions stopped starting jobs at 06:30 Bangkok ("recent account payments
have failed or your spending limit needs to be increased"). Once billing is fixed:
1. daily-sync, league "V.League 1", live (enrich).
2. fotmob-squads club=indonesia dry run, then live; then malaysia, singapore, vietnam.
Philippines PFL (FootyStats 17881) exists but is not yet chosen on the account.
