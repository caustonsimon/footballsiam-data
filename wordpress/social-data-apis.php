/**
 * FOOTBALL SIAM - SOCIAL DATA APIs (v2, 2026-11-06)
 *
 * v2 (2026-11-06): FIXED a bug in /social-table-data that returned the wrong
 * five results in `form`. `fs_build_table()` returns each club's form array
 * NEWEST FIRST, so `array_slice($t['form'], -5)` was taking the five OLDEST
 * results, not the five most recent. Every club in the division was affected:
 * checked against all sixteen clubs' own results on 6 November 2026, the
 * endpoint matched the reverse of each club's FIRST five matches 16 times out
 * of 16, and the correct last five 0 times out of 16. Chonburi were reported
 * as `L L L L L` having just won two in a row; Ratchaburi were shown with a
 * defeat as their latest result four days after winning 5-2 at Sisaket United.
 * The fix is `array_slice($t['form'] ?? [], 0, 5)` -- the FIRST five of a
 * newest-first array. Nothing else in this file changed.
 *
 * NOTE FOR FUTURE EDITS: the correctness of that line depends entirely on
 * `fs_build_table()` returning form newest-first. If that function is ever
 * changed to return oldest-first, this line must become
 * `array_reverse(array_slice($t['form'] ?? [], -5))`. The other fields on this
 * endpoint (pos, p, w, d, l, gf, ga, gd, pts) were verified correct against the
 * site's own table for all sixteen clubs at the same time and were not touched.
 *
 * Verify after installing (the site answers 403 to a bare user agent, so send a
 * browser one):
 *   curl -s -A "Mozilla/5.0" \
 *     "https://footballsiam.com/wp-json/fs/v1/social-table-data?nocache=$(date +%s)"
 * Each club's `form` should read newest first, so its first letter is the
 * result of that club's most recent match.
 *
 * ---------------------------------------------------------------------------
 *
 * Purpose: JSON data endpoints for the fully-automated Guardian-style
 * social carousel (table + fixtures/results), built after discovering
 * that screenshotting the site's own HTML cards (fs_social_table /
 * fs_social_fixtures shortcodes) cannot be automated reliably --
 * footballsiam.com sits behind SiteGround's anti-bot challenge
 * (sgcaptcha), which blocks plain HTTP and headless-browser requests
 * from datacenter IPs. Confirmed 2026-09-16 by direct test: both curl
 * and a real headless Chromium (Playwright) from a cloud sandbox got
 * served the "Robot Challenge Screen" instead of real content. Only
 * WebFetch (a different fetch path Claude has access to) reliably
 * reaches the site -- and WebFetch converts pages to text, it cannot
 * take a screenshot.
 *
 * So instead of screenshotting HTML, the automated social pipeline now
 * fetches plain JSON via WebFetch (proven reliable -- this is exactly
 * how the existing Attendance/Nationality Buffer automations already
 * work) and renders the finished card as an image directly in Python
 * (matplotlib/PIL), matching the visual language of the fs_social_table
 * / fs_social_fixtures shortcodes as closely as possible. These three
 * routes are that JSON layer. They deliberately reuse the SAME
 * functions the live HTML shortcodes use (fs_build_table(),
 * fs_get_matches_v3()) -- one authoritative data layer, two different
 * renderers (HTML for the live site, Python for social).
 *
 * New REST routes:
 *   GET /wp-json/fs/v1/social-table-data?league=thai-league-1&season=2026/2027
 *     -> { league, season, standings: [ {pos, name, p, w, d, l, gf, ga, gd, pts, form}, ... ] }
 *     (form is the club's last five results, NEWEST FIRST.)
 *
 *   GET /wp-json/fs/v1/social-fixtures-data?league=thai-league-1&season=2026/2027&mw=N&mode=fixtures|results
 *     -> { league, season, mw, mode, matches: [ {date, time, ts, home, away, home_score, away_score, is_scored}, ... ] }
 *     (date is a plain "Friday 18 September"-style label, pre-formatted server-side
 *     so the Python renderer doesn't need its own date-grouping logic.)
 *
 *   GET /wp-json/fs/v1/current-matchweek?league=thai-league-1&season=2026/2027
 *     -> { league, season, mw, earliest_kickoff }
 *     Picks whichever matchweek's earliest fixture is the most recent one
 *     that has already started, or is due within the next 3 days -- i.e.
 *     "the matchweek this Thu-Sun posting cycle is about," working
 *     correctly on Thursday (before kickoff) through Sunday night.
 */

add_action('rest_api_init', function () {
    register_rest_route('fs/v1', '/social-table-data', [
        'methods'  => 'GET',
        'callback' => 'fs_social_table_data_callback',
        'permission_callback' => '__return_true',
        'args' => [
            'league' => ['default' => 'thai-league-1'],
            'season' => ['default' => '2026/2027'],
        ],
    ]);

    register_rest_route('fs/v1', '/social-fixtures-data', [
        'methods'  => 'GET',
        'callback' => 'fs_social_fixtures_data_callback',
        'permission_callback' => '__return_true',
        'args' => [
            'league' => ['default' => 'thai-league-1'],
            'season' => ['default' => '2026/2027'],
            'mw'     => ['default' => ''],
            'mode'   => ['default' => 'fixtures'],
        ],
    ]);

    register_rest_route('fs/v1', '/current-matchweek', [
        'methods'  => 'GET',
        'callback' => 'fs_current_matchweek_callback',
        'permission_callback' => '__return_true',
        'args' => [
            'league' => ['default' => 'thai-league-1'],
            'season' => ['default' => '2026/2027'],
        ],
    ]);
});

function fs_social_table_data_callback($request) {
    $league = $request->get_param('league');
    $season = $request->get_param('season');

    if (!function_exists('fs_build_table')) {
        return new WP_Error('fs_missing_dependency', 'fs_build_table() not found -- Home Page Master snippet must be active.', ['status' => 500]);
    }

    $teams = fs_build_table($league, $season);
    $standings = [];
    $pos = 1;
    foreach ($teams as $t) {
        $standings[] = [
            'pos'  => $pos++,
            'name' => $t['name'],
            'p'    => $t['p'],
            'w'    => $t['w'],
            'd'    => $t['d'],
            'l'    => $t['l'],
            'gf'   => $t['gf'],
            'ga'   => $t['ga'],
            'gd'   => $t['gd'],
            // fs_build_table() returns form NEWEST FIRST, so the five most
            // recent results are the FIRST five, not the last five. Using
            // -5 here returned each club's five OLDEST results all season
            // (fixed 2026-11-06; see the header note).
            'form' => array_slice($t['form'] ?? [], 0, 5),
        ];
    }

    return [
        'league'    => $league,
        'season'    => $season,
        'standings' => $standings,
    ];
}

function fs_social_fixtures_data_callback($request) {
    $league = $request->get_param('league');
    $season = $request->get_param('season');
    $mw     = (string) $request->get_param('mw');
    $mode   = ($request->get_param('mode') === 'results') ? 'results' : 'fixtures';

    if (!function_exists('fs_get_matches_v3')) {
        return new WP_Error('fs_missing_dependency', 'fs_get_matches_v3() not found -- Home Page Master snippet must be active.', ['status' => 500]);
    }

    $raw = fs_get_matches_v3($mode, $league, -1, $mw, '', '', $season);

    $matches = [];
    foreach ($raw as $m) {
        $home_name = get_the_title($m['hid']) ?: $m['hname'];
        $away_name = get_the_title($m['aid']) ?: $m['aname'];
        $is_scored = ($m['hs'] !== '' && $m['hs'] !== null && $m['hs'] !== false);

        $matches[] = [
            'date'        => $m['ts'] ? date_i18n('l j F', $m['ts']) : '',
            'time'        => $m['time'],
            'ts'          => $m['ts'],
            'home'        => $home_name,
            'away'        => $away_name,
            'home_score'  => $is_scored ? (int) $m['hs'] : null,
            'away_score'  => $is_scored ? (int) $m['as'] : null,
            'is_scored'   => $is_scored,
        ];
    }

    return [
        'league'  => $league,
        'season'  => $season,
        'mw'      => $mw,
        'mode'    => $mode,
        'matches' => $matches,
    ];
}

function fs_current_matchweek_callback($request) {
    $league = $request->get_param('league');
    $season = $request->get_param('season');

    if (!function_exists('fs_get_matches_v3')) {
        return new WP_Error('fs_missing_dependency', 'fs_get_matches_v3() not found -- Home Page Master snippet must be active.', ['status' => 500]);
    }

    $results  = fs_get_matches_v3('results', $league, -1, '', '', '', $season);
    $fixtures = fs_get_matches_v3('fixtures', $league, -1, '', '', '', $season);
    $all = array_merge($results, $fixtures);

    if (empty($all)) {
        return new WP_Error('fs_no_matches', 'No matches found for this league/season.', ['status' => 404]);
    }

    $mw_min_ts = [];
    foreach ($all as $m) {
        $mw = $m['mw'];
        if ($mw === '' || $mw === null) continue;
        if (!isset($mw_min_ts[$mw]) || $m['ts'] < $mw_min_ts[$mw]) {
            $mw_min_ts[$mw] = $m['ts'];
        }
    }

    $now = time();
    $cutoff = $now + (3 * DAY_IN_SECONDS);

    $best_mw = null;
    $best_ts = null;
    foreach ($mw_min_ts as $mw => $min_ts) {
        if ($min_ts <= $cutoff) {
            if ($best_ts === null || $min_ts > $best_ts) {
                $best_ts = $min_ts;
                $best_mw = $mw;
            }
        }
    }

    // Fallback: if every matchweek starts more than 3 days from now,
    // just take the earliest upcoming one so the endpoint never returns
    // nothing.
    if ($best_mw === null) {
        asort($mw_min_ts);
        $best_mw = array_key_first($mw_min_ts);
        $best_ts = $mw_min_ts[$best_mw];
    }

    return [
        'league'           => $league,
        'season'           => $season,
        'mw'               => $best_mw,
        'earliest_kickoff' => $best_ts ? date('Y-m-d H:i', $best_ts) : null,
    ];
}
