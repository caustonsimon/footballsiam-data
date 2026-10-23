<?php
/**
 * FS Club Hub v1  (added 2026-10-21, Port first)
 *
 * Replaces the Divi club template with a full club hub for clubs whose squad
 * is built from FotMob (src/fotmob_squads.py writes club meta
 * fs_club_hub_json). Sections: hero with league position and form, next /
 * last match, season numbers, squad by position, sortable squad stats with a
 * competition filter, team stats (league xG and shots by match, averages),
 * fixtures and results across every competition, the league table around
 * the club, club information and map, matchday tours.
 *
 * Only clubs listed in fs_club_hub_enabled() switch over; every other club
 * page is untouched. Remove an id (or deactivate) to fall back to the old
 * page. Reuses Snippet 146 (fs_get_club_matches_v18, fs_club_resolve_id_v18)
 * and Snippet 157 (fs_build_table, fs_get_club_logo) when they are active.
 */

if ( ! function_exists( 'fs_club_hub_enabled' ) ) {

function fs_club_hub_enabled() {
	// Every Thai League 1 club (rolled out 22 Oct 2026) and every Thai League 2
	// club (22 Oct 2026). A club with no FotMob feed yet keeps its old page.
	return array( 7937, 7934, 7929, 7928, 7933, 7930, 7931, 7938, 7939, 7942, 7961, 7959, 7941, 7935, 7940, 7953,
		7956, 7951, 7954, 7945, 7932, 7952, 7958, 7949, 7955, 7960, 7948, 7947, 7936, 9792, 13879, 7944, 7943, 9862 );
}

/* ------------------------------------------------------------------------
 * Data
 * --------------------------------------------------------------------- */

function fs_hub_age( $dob ) {
	if ( ! $dob ) return '';
	try {
		$d = new DateTime( $dob, new DateTimeZone( 'Asia/Bangkok' ) );
		$n = new DateTime( 'now', new DateTimeZone( 'Asia/Bangkok' ) );
		return (string) $d->diff( $n )->y;
	} catch ( Exception $e ) { return ''; }
}

function fs_hub_matches( $cid, $type, $names = array() ) {
	// This season only, every competition (own query: Snippet 146's getter
	// caps at 40 posts across all seasons). Thai League 3 fixtures carry club
	// NAMES, not club ids (22 Oct 2026): $names lists every spelling the
	// fixtures use for this club (stored FootyStats name + Snippet 153's
	// corrected one).
	$who = $names
		? array( 'relation' => 'OR', array( 'key' => 'home_team_name', 'value' => $names, 'compare' => 'IN' ), array( 'key' => 'away_team_name', 'value' => $names, 'compare' => 'IN' ) )
		: array( 'relation' => 'OR',
			array( 'key' => 'home_team', 'value' => (string) $cid ), array( 'key' => 'away_team', 'value' => (string) $cid ),
			array( 'key' => 'home_team', 'value' => '"' . $cid . '"', 'compare' => 'LIKE' ), array( 'key' => 'away_team', 'value' => '"' . $cid . '"', 'compare' => 'LIKE' ) );
	$ids = get_posts( array(
		'post_type' => 'matches', 'post_status' => 'publish', 'posts_per_page' => -1, 'fields' => 'ids', 'no_found_rows' => true,
		'meta_query' => array( 'relation' => 'AND',
			array( 'key' => 'season', 'value' => array( '2026/2027', '2026/27' ), 'compare' => 'IN' ), $who ),
	) );
	$rows = array();
	foreach ( $ids as $pid ) {
		$hs = get_post_meta( $pid, 'home_score', true );
		$played = ( $hs !== '' && $hs !== null && $hs !== false );
		if ( ( $type === 'results' ) !== $played ) continue;
		if ( $type === 'fixtures' && get_post_meta( $pid, 'match_status', true ) ) continue; // postponed etc.
		$ymd = (string) get_post_meta( $pid, 'date', true );
		$ko  = (string) get_post_meta( $pid, 'kick_off_time', true );
		$ts  = $ymd ? strtotime( substr( $ymd, 0, 4 ) . '-' . substr( $ymd, 4, 2 ) . '-' . substr( $ymd, 6, 2 ) . ' ' . ( $ko ?: '00:00' ) . ' Asia/Bangkok' ) : 0;
		if ( ! $ts ) continue;
		$comp = 'Match';
		$terms = wp_get_post_terms( $pid, 'competition' );
		if ( ! is_wp_error( $terms ) && $terms ) $comp = $terms[0]->name;
		$res_id = function ( $v ) { if ( is_array( $v ) ) $v = reset( $v ); $v = trim( (string) $v, '"[] ' ); return (int) $v; };
		$rows[] = array(
			'ID' => $pid, 'hs' => $hs, 'as' => get_post_meta( $pid, 'away_score', true ), 'ts' => $ts,
			'time' => ( $ko && $ko !== '00:00' ) ? substr( $ko, 0, 5 ) : 'TBC', 'mw' => get_post_meta( $pid, 'match_week', true ),
			'hid' => $res_id( maybe_unserialize( get_post_meta( $pid, 'home_team', true ) ) ), 'aid' => $res_id( maybe_unserialize( get_post_meta( $pid, 'away_team', true ) ) ),
			'comp' => $comp, 'hname' => get_post_meta( $pid, 'home_team_name', true ), 'aname' => get_post_meta( $pid, 'away_team_name', true ),
		);
	}
	usort( $rows, function ( $x, $y ) use ( $type ) { return $type === 'fixtures' ? $x['ts'] - $y['ts'] : $y['ts'] - $x['ts']; } );
	$out  = array();
	foreach ( $rows as $m ) {
		if ( $names ) {
			$home   = in_array( (string) $m['hname'], $names, true ) || ( (int) $m['hid'] === (int) $cid );
			$opp    = $home ? $m['aname'] : $m['hname'];
			$opp_id = $home ? $m['aid'] : $m['hid'];
			if ( ! $opp_id ) $opp_id = fs_hub_club_id_by_name( $opp );
			if ( $opp_id && ! $opp ) $opp = get_the_title( $opp_id );
		} else {
		$home  = ( (int) $m['hid'] === (int) $cid );
		$opp_id = $home ? $m['aid'] : $m['hid'];
		$opp    = $opp_id ? get_the_title( $opp_id ) : ( $home ? $m['aname'] : $m['hname'] );
		}
		$comp   = $m['comp'];
		$round  = '';
		$terms  = wp_get_post_terms( $m['ID'], 'competition', array( 'fields' => 'slugs' ) );
		$slug   = ( ! is_wp_error( $terms ) && $terms ) ? $terms[0] : '';
		if ( $slug === 'thai-fa-cup' && function_exists( 'fs_facup_round_label' ) ) {
			$round = fs_facup_round_label( $m['mw'], false );
		} elseif ( is_numeric( $m['mw'] ) && $m['mw'] > 0 ) {
			$round = ( ( in_array( $slug, array( 'thai-league-1', 'thai-league-2', 'indonesian-super-league', 'malaysia-super-league', 'singapore-premier-league', 'v-league-1' ), true ) || strpos( $slug, 't3_' ) === 0 ) ? 'Matchweek ' : 'Matchday ' ) . $m['mw'];
		}
		$res = '';
		if ( $type === 'results' && $m['hs'] !== '' ) {
			$gf = (int) ( $home ? $m['hs'] : $m['as'] );
			$ga = (int) ( $home ? $m['as'] : $m['hs'] );
			$res = $gf > $ga ? 'W' : ( $gf < $ga ? 'L' : 'D' );
		}
		$out[] = array(
			'id' => $m['ID'], 'url' => get_permalink( $m['ID'] ), 'ts' => $m['ts'],
			'time' => $m['time'], 'home' => $home, 'opp' => $opp ?: 'TBC',
			'opp_logo' => ( $opp_id && function_exists( 'fs_get_club_logo' ) ) ? fs_get_club_logo( $opp_id ) : '',
			'comp' => $comp, 'slug' => $slug, 'round' => $round, 'hs' => $m['hs'], 'as' => $m['as'], 'res' => $res,
		);
	}
	return $out;
}

function fs_hub_club_id_by_name( $name ) {
	static $map = null;
	if ( $map === null ) {
		$map = array();
		foreach ( get_posts( array( 'post_type' => 'club', 'posts_per_page' => -1, 'fields' => 'ids', 'post_status' => 'publish' ) ) as $id ) $map[ get_the_title( $id ) ] = $id;
	}
	return isset( $map[ $name ] ) ? $map[ $name ] : 0;
}

function fs_hub_table( $cid, $slug = 'thai-league-1' ) {
	if ( ! function_exists( 'fs_build_table' ) ) return array();
	$t = fs_build_table( $slug );
	if ( ! is_array( $t ) ) return array();
	$t = array_values( $t );
	usort( $t, function ( $a, $b ) {
		if ( $a['pts'] !== $b['pts'] ) return $b['pts'] - $a['pts'];
		$gda = $a['gf'] - $a['ga']; $gdb = $b['gf'] - $b['ga'];
		if ( $gda !== $gdb ) return $gdb - $gda;
		return $b['gf'] - $a['gf'];
	} );
	foreach ( $t as $i => &$r ) { $r['pos'] = $i + 1; $r['gd'] = $r['gf'] - $r['ga']; $r['me'] = ( (int) $r['id'] === (int) $cid ) || ( $r['name'] === get_the_title( $cid ) ); if ( empty( $r['id'] ) && function_exists( 'fs_hub_club_id_by_name' ) ) $r['id'] = fs_hub_club_id_by_name( $r['name'] ); }
	return $t;
}

/** Regional Thai League 3 table from the region's own results, by club name
 *  (fs_build_table does not filter T3 regions when raw FootyStats data is
 *  stored, and T3 fixtures carry names rather than club ids). */
function fs_hub_name_table( $slug, $names ) {
	$ids = get_posts( array(
		'post_type' => 'matches', 'post_status' => 'publish', 'posts_per_page' => -1, 'fields' => 'ids', 'no_found_rows' => true,
		'tax_query' => array( array( 'taxonomy' => 'competition', 'field' => 'slug', 'terms' => array( $slug ) ) ),
		'meta_query' => array( array( 'key' => 'season', 'value' => array( '2026/2027', '2026/27' ), 'compare' => 'IN' ) ),
	) );
	$t = array();
	foreach ( $ids as $pid ) {
		$hn = (string) get_post_meta( $pid, 'home_team_name', true ); $an = (string) get_post_meta( $pid, 'away_team_name', true );
		if ( $hn === '' || $an === '' ) continue;
		foreach ( array( $hn, $an ) as $n ) if ( ! isset( $t[ $n ] ) ) $t[ $n ] = array( 'name' => $n, 'id' => fs_hub_club_id_by_name( $n ), 'p' => 0, 'w' => 0, 'd' => 0, 'l' => 0, 'gf' => 0, 'ga' => 0, 'pts' => 0 );
		$hs = get_post_meta( $pid, 'home_score', true ); $as = get_post_meta( $pid, 'away_score', true );
		if ( $hs === '' || $hs === null || $hs === false || $as === '' || $as === null ) continue;
		$hs = (int) $hs; $as = (int) $as;
		foreach ( array( array( $hn, $hs, $as ), array( $an, $as, $hs ) ) as $x ) {
			$r = &$t[ $x[0] ]; $r['p']++; $r['gf'] += $x[1]; $r['ga'] += $x[2];
			if ( $x[1] > $x[2] ) { $r['w']++; $r['pts'] += 3; } elseif ( $x[1] === $x[2] ) { $r['d']++; $r['pts']++; } else $r['l']++;
			unset( $r );
		}
	}
	$t = array_values( $t );
	usort( $t, function ( $a, $b ) { return ( $b['pts'] - $a['pts'] ) ?: ( ( $b['gf'] - $b['ga'] ) - ( $a['gf'] - $a['ga'] ) ) ?: ( $b['gf'] - $a['gf'] ); } );
	foreach ( $t as $i => &$r ) { $r['pos'] = $i + 1; $r['gd'] = $r['gf'] - $r['ga']; $r['me'] = in_array( $r['name'], $names, true ); }
	unset( $r );
	return $t;
}

function fs_hub_data( $cid ) {
	$raw = get_post_meta( $cid, 'fs_club_hub_json', true );
	$hub = $raw ? json_decode( $raw, true ) : null;
	if ( ! is_array( $hub ) || empty( $hub['squad'] ) ) return null;

	$f = function ( $k ) use ( $cid ) { return function_exists( 'get_field' ) ? get_field( $k, $cid ) : get_post_meta( $cid, $k, true ); };
	$logo = get_the_post_thumbnail_url( $cid, 'full' );
	if ( ! $logo && function_exists( 'fs_get_club_logo' ) ) $logo = fs_get_club_logo( $cid );
	if ( ! $logo && ! empty( $hub['logo'] ) ) $logo = $hub['logo']; // Thai League registry crest (T3)
	$names = ! empty( $hub['match_names'] ) ? array_values( array_unique( array_merge( (array) $hub['match_names'], array( get_the_title( $cid ) ) ) ) ) : array();
	$coach_pid = (int) get_post_meta( $cid, 'head_coach_post_id', true );

	foreach ( $hub['squad'] as &$p ) {
		$p['url']   = $p['wp_id'] ? get_permalink( $p['wp_id'] ) : '';
		$p['age']   = fs_hub_age( $p['dob'] );
		$photo      = $p['wp_id'] ? get_the_post_thumbnail_url( $p['wp_id'], 'medium' ) : '';
		if ( ! $photo && $p['wp_id'] && function_exists( 'get_field' ) ) $photo = get_field( 'player_photo_url', $p['wp_id'] );
		if ( ! $photo && ! empty( $p['photo_src'] ) ) $photo = $p['photo_src'];
		$p['photo'] = $photo ?: '';
	}
	unset( $p );

	return array(
		'cid' => $cid, 'name' => get_the_title( $cid ), 'logo' => $logo,
		'nickname' => $f( 'club_nickname' ), 'stadium' => $f( 'stadium_name' ) ?: ( isset( $hub['venue'] ) ? $hub['venue'] : '' ),
		'capacity' => $f( 'capacity' ), 'city' => $f( 'city' ), 'founded' => $f( 'founded_year' ),
		'owner' => $f( 'owner' ), 'last_finish' => $f( 'last_season_finish' ),
		'coach' => $f( 'head_coach' ),
		'coach_url' => ( $coach_pid && get_post_type( $coach_pid ) === 'head_coach' ) ? get_permalink( $coach_pid ) : '',
		'hub' => $hub,
		'fixtures' => fs_hub_matches( $cid, 'fixtures', $names ),
		'results' => fs_hub_matches( $cid, 'results', $names ),
		'league' => ! empty( $hub['league'] ) ? $hub['league'] : 'Thai League 1',
		'table' => $names ? fs_hub_name_table( $hub['league_slug'], $names ) : fs_hub_table( $cid, ! empty( $hub['league_slug'] ) ? $hub['league_slug'] : 'thai-league-1' ),
		'tours_url' => home_url( '/football-siam-tours/' ),
	);
}

/* ------------------------------------------------------------------------
 * Render (pure: takes the data array, returns HTML)
 * --------------------------------------------------------------------- */

function fs_hub_e( $s ) { return htmlspecialchars( (string) $s, ENT_QUOTES, 'UTF-8' ); }

function fs_hub_date( $ts, $fmt = 'D j M' ) {
	$d = new DateTime( '@' . (int) $ts ); $d->setTimezone( new DateTimeZone( 'Asia/Bangkok' ) );
	return $d->format( $fmt );
}

function fs_hub_comp_short( $c ) {
	$map = array(
		'Thai League 1' => 'Thai League 1', 'Thai League 2' => 'Thai League 2', 'AFC Champions League Elite' => 'ACL Elite',
		'AFC Champions League Two' => 'ACL Two', 'ASEAN Club Championship' => 'ASEAN Club Champ.',
		'Thai FA Cup' => 'FA Cup', 'Thai League Cup' => 'League Cup',
		'Thai League North' => 'Thai League 3 North', 'Thai League Central' => 'Thai League 3 Central',
	);
	return isset( $map[ $c ] ) ? $map[ $c ] : $c;
}

function fs_hub_render( $d ) {
	$e   = 'fs_hub_e';
	$hub = $d['hub'];
	$sq  = $hub['squad'];
	$mt  = isset( $hub['team']['matches'] ) ? $hub['team']['matches'] : array();
	$res = $d['results']; $fix = $d['fixtures'];

	// league position
	$me = null;
	foreach ( $d['table'] as $r ) if ( $r['me'] ) $me = $r;
	$ord = function ( $n ) { $n = (int) $n; $s = array( 'th', 'st', 'nd', 'rd' ); $v = $n % 100; return $n . ( isset( $s[ ( $v - 20 ) % 10 ] ) ? $s[ ( $v - 20 ) % 10 ] : ( isset( $s[ $v ] ) ? $s[ $v ] : $s[0] ) ); };

	// form: last five results, any competition (results are newest first)
	$form = array_slice( $res, 0, 5 );

	// season record, all competitions
	$rec = array( 'p' => 0, 'w' => 0, 'd' => 0, 'l' => 0, 'gf' => 0, 'ga' => 0 );
	foreach ( $res as $r ) {
		$rec['p']++; $rec[ strtolower( $r['res'] ) ]++;
		$rec['gf'] += (int) ( $r['home'] ? $r['hs'] : $r['as'] );
		$rec['ga'] += (int) ( $r['home'] ? $r['as'] : $r['hs'] );
	}

	// leaders
	$pick = function ( $key, $min_mins = 0 ) use ( $sq ) {
		$best = null;
		foreach ( $sq as $p ) {
			$s = $p['stats']; if ( ! $s ) continue;
			if ( $s['mins'] < $min_mins ) continue;
			$v = $s[ $key ]; if ( $v === null || $v <= 0 ) continue;
			if ( ! $best || $v > $best['stats'][ $key ] ) $best = $p;
		}
		return $best;
	};
	$leaders = array(
		array( 'Top scorer', $pick( 'goals' ), 'goals', 'goals' ),
		array( 'Most assists', $pick( 'assists' ), 'assists', 'assists' ),
		array( 'Highest rated', $pick( 'rating', 270 ), 'rating', 'avg rating' ),
		array( 'Most minutes', $pick( 'mins' ), 'mins', 'minutes' ),
	);

	// league averages from FotMob team stats
	$league = $d['league'];
	$lg = array_values( array_filter( $mt, function ( $m ) use ( $league ) { return $m['comp'] === $league; } ) );
	$avg = function ( $k ) use ( $lg ) {
		$v = array(); foreach ( $lg as $m ) if ( isset( $m['stats'][ $k ] ) && $m['stats'][ $k ] !== '' ) $v[] = (float) $m['stats'][ $k ];
		return $v ? array_sum( $v ) / count( $v ) : null;
	};
	$tiles = array(
		array( 'xG per game', $avg( 'Expected goals (xG)' ), 2, '' ),
		array( 'Possession', $avg( 'Ball possession' ), 0, '%' ),
		array( 'Shots per game', $avg( 'Total shots' ), 1, '' ),
		array( 'On target per game', $avg( 'Shots on target' ), 1, '' ),
		array( 'Big chances per game', $avg( 'Big chances' ), 1, '' ),
		array( 'Corners per game', $avg( 'Corners' ), 1, '' ),
	);

	// FotMob has no xG/shot data or match ratings for some leagues (Thai League 2):
	// hide those blocks rather than draw rows of zeros.
	$has_xg = false; $has_shots = false;
	foreach ( $lg as $m ) { if ( isset( $m['stats']['Expected goals (xG)'] ) ) $has_xg = true; if ( isset( $m['stats']['Total shots'] ) ) $has_shots = true; }
	$has_tiles = false; foreach ( $tiles as $T ) if ( $T[1] !== null ) $has_tiles = true;
	$has_rating = false; foreach ( $sq as $p ) if ( ! empty( $p['stats']['rating'] ) ) $has_rating = true;
	$has_ast = false; foreach ( $sq as $p ) if ( ! empty( $p['stats']['assists'] ) ) $has_ast = true;
	if ( ! empty( $hub['no_assists'] ) ) $has_ast = false;

	// groups
	$groups = array( 'GK' => 'Goalkeepers', 'DF' => 'Defenders', 'MF' => 'Midfielders', 'FW' => 'Forwards' );
	$by = array();
	foreach ( $sq as $p ) $by[ $p['pos'] ?: 'MF' ][] = $p;
	foreach ( $by as &$g ) usort( $g, function ( $a, $b ) { return (int) $a['number'] - (int) $b['number']; } );
	unset( $g );

	$comps = array();
	foreach ( $sq as $p ) foreach ( array_keys( (array) $p['by_comp'] ) as $c ) $comps[ $c ] = true;
	$comp_order = array( 'Thai League 1', 'Thai League 2', 'AFC Champions League Elite', 'AFC Champions League Two', 'ASEAN Club Championship', 'Thai FA Cup', 'Thai League Cup' );
	$comps = array_values( array_unique( array_merge( isset( $comps[ $d['league'] ] ) ? array( $d['league'] ) : array(), array_filter( $comp_order, function ( $c ) use ( $comps ) { return isset( $comps[ $c ] ); } ), array_keys( $comps ) ) ) );

	$next = $fix ? $fix[0] : null; $last = $res ? $res[0] : null;
	$season = $hub['season'];
	$upd = $hub['updated'];

	ob_start();
	?>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Lato:wght@400;700;900&family=Playfair+Display:wght@700;900&family=Noto+Sans+Thai:wght@600&display=swap">
<div class="fsh" id="fsh-top">
<style>
.fsh{--g:#0A3B25;--gd:#e3c567;--ink:#111;--tx:#333;--mu:#767676;--hl:#e6e6e6;--tint:#f6f6f4;font-family:'Lato',sans-serif;color:var(--tx);background:#fdfdfd}
.fsh *{box-sizing:border-box}
.fsh a{color:inherit}
.fsh-wrap{max-width:1180px;margin:0 auto;padding:0 20px}
.fsh-hero{background:var(--g);color:#fff;position:relative;overflow:hidden}
.fsh-hero:after{content:"";position:absolute;left:0;right:0;bottom:0;height:6px;background:var(--gd)}
.fsh-ele{position:absolute;right:-30px;bottom:-40px;height:300px;opacity:.09;pointer-events:none}
.fsh-hero-in{position:relative;z-index:1;display:grid;grid-template-columns:auto 1fr auto;gap:28px;align-items:center;padding:42px 20px 48px;max-width:1180px;margin:0 auto}
.fsh-crest{width:118px;height:118px;background:#fff;border-radius:50%;display:flex;align-items:center;justify-content:center;box-shadow:0 0 0 4px var(--gd)}
.fsh-crest img{width:84px;height:84px;object-fit:contain}
.fsh-kick{color:var(--gd);font-weight:900;letter-spacing:2px;text-transform:uppercase;font-size:12px}
.fsh-h1{font-family:'Playfair Display',serif;font-weight:900;text-transform:uppercase;font-size:56px;line-height:1;margin:8px 0 10px;color:#fff!important;letter-spacing:.5px}
.fsh-meta{display:flex;flex-wrap:wrap;gap:6px 22px;font-size:13px;opacity:.92}
.fsh-meta b{color:var(--gd);font-weight:700;text-transform:uppercase;font-size:10px;letter-spacing:1px;margin-right:6px}
.fsh-pos{text-align:right}
.fsh-pos .n{font-family:'Playfair Display',serif;font-weight:900;font-size:76px;line-height:.9;color:var(--gd)}
.fsh-pos .l{font-size:11px;letter-spacing:1.5px;text-transform:uppercase;opacity:.85;margin-top:12px}
.fsh-pos .pts{font-size:13px;margin-top:8px}
.fsh-form{display:flex;gap:5px;justify-content:flex-end;margin-top:10px}
.fsh-fp{width:24px;height:24px;border-radius:4px;display:flex;align-items:center;justify-content:center;font-weight:900;font-size:11px;text-decoration:none}
.fsh .fsh-W{background:#1f8a4c;color:#fff}.fsh .fsh-D{background:#c9c9c9;color:#111}.fsh .fsh-L{background:#d93025;color:#fff}
.fsh-nav{position:sticky;top:0;z-index:20;background:#fff;border-bottom:1px solid var(--hl);box-shadow:0 2px 6px rgba(0,0,0,.04)}
.fsh-nav .fsh-wrap{display:flex;gap:4px;overflow-x:auto;scrollbar-width:none}
.fsh-nav a{padding:15px 14px;font-weight:900;font-size:12px;letter-spacing:1px;text-transform:uppercase;text-decoration:none;color:var(--ink);border-bottom:3px solid transparent;white-space:nowrap}
.fsh-nav a:hover,.fsh-nav a.on{border-bottom-color:var(--gd);color:var(--g)}
.fsh-sec{padding:38px 0 6px;scroll-margin-top:60px}
.fsh-h2{font-family:'Playfair Display',serif;font-weight:900;text-transform:uppercase;font-size:26px;color:var(--ink);margin:0 0 4px;line-height:1.15}
.fsh-rule{width:70px;height:4px;background:var(--gd);margin:8px 0 20px}
.fsh-sub{color:var(--mu);font-size:13px;margin:-12px 0 18px}
.fsh-card{background:#fff;border-radius:8px;box-shadow:0 2px 5px rgba(0,0,0,.05);border:1px solid #eee}
.fsh-g2{display:grid;grid-template-columns:1fr 1fr;gap:18px}
.fsh-g4{display:grid;grid-template-columns:repeat(4,1fr);gap:14px}
.fsh-g6{display:grid;grid-template-columns:repeat(6,1fr);gap:12px}
.fsh-match{padding:20px;border-top:4px solid var(--g)}
.fsh-match .lbl{font-size:11px;font-weight:900;letter-spacing:1.5px;text-transform:uppercase;color:var(--g)}
.fsh-match .cmp{font-size:12px;color:var(--mu);margin-top:3px}
.fsh-vs{display:grid;grid-template-columns:1fr auto 1fr;align-items:center;gap:12px;margin:16px 0 6px}
.fsh-vs .t{font-family:'Playfair Display',serif;font-weight:900;font-size:18px;text-transform:uppercase;color:var(--ink);display:flex;align-items:center;gap:8px}
.fsh-vs .t.r{justify-content:flex-end;text-align:right}
.fsh-vs img{width:30px;height:30px;object-fit:contain}
.fsh .fsh-sc{background:var(--g);color:#fff;font-weight:900;font-size:20px;padding:8px 14px;border-radius:5px;min-width:74px;text-align:center;text-decoration:none}
.fsh-match .foot{font-size:12px;color:var(--mu);text-align:center}
.fsh-rec{display:flex;gap:0;margin-top:18px}
.fsh-rec div{flex:1;text-align:center;padding:12px 4px;border-right:1px solid #eee}
.fsh-rec div:last-child{border-right:0}
.fsh-rec b{display:block;font-family:'Playfair Display',serif;font-weight:900;font-size:28px;color:var(--ink)}
.fsh-rec span{font-size:10px;letter-spacing:1px;text-transform:uppercase;color:var(--mu)}
.fsh-lead{padding:16px;display:flex;gap:12px;align-items:center;border-left:4px solid var(--gd)}
.fsh-lead .v{font-family:'Playfair Display',serif;font-weight:900;font-size:34px;color:var(--g);line-height:1;min-width:56px}
.fsh-lead .k{font-size:10px;letter-spacing:1.5px;text-transform:uppercase;color:var(--mu);font-weight:900}
.fsh-lead .p{font-weight:900;color:var(--ink);font-size:15px;text-decoration:none}
.fsh-lead .u{font-size:11px;color:var(--mu)}
.fsh-grp{margin-bottom:24px}
.fsh-grp h3{font-size:12px;font-weight:900;letter-spacing:2px;text-transform:uppercase;color:var(--g);margin:0 0 10px;padding-bottom:6px;border-bottom:2px solid var(--g);display:flex;justify-content:space-between}
.fsh-grp h3 span{color:var(--mu);font-weight:700}
.fsh-pl{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:12px}
.fsh-p{display:flex;gap:12px;align-items:center;padding:12px;text-decoration:none;transition:transform .12s,box-shadow .12s}
.fsh-p:hover{transform:translateY(-2px);box-shadow:0 6px 14px rgba(0,0,0,.08)}
.fsh-no{flex-shrink:0;width:48px;height:48px;border-radius:6px;background:var(--g);color:var(--gd);font-family:'Playfair Display',serif;font-weight:900;font-size:22px;display:flex;align-items:center;justify-content:center;overflow:hidden;position:relative}
.fsh-no img{width:100%;height:100%;object-fit:cover;object-position:top}
.fsh-no i{position:absolute;right:0;bottom:0;background:var(--gd);color:var(--g);font-style:normal;font-size:10px;padding:1px 4px;border-radius:4px 0 0 0;font-family:'Lato',sans-serif}
.fsh-pn{font-weight:900;color:var(--ink);font-size:14px;line-height:1.25}
.fsh-pth{font-family:'Noto Sans Thai',sans-serif;font-weight:600;color:var(--g);font-size:12px;line-height:1.4}
.fsh-pm{font-size:11px;color:var(--mu);margin-top:2px}
.fsh-ps{font-size:11px;color:var(--tx);margin-top:5px;display:flex;gap:10px}
.fsh-ps b{color:var(--g)}
.fsh-rt{margin-left:auto;flex-shrink:0;font-weight:900;font-size:12px;padding:4px 7px;border-radius:4px;background:#eef3ef;color:var(--g)}
.fsh-rt.hi{background:var(--g);color:#fff}.fsh-rt.top{background:var(--gd);color:var(--g)}
.fsh-inj{font-size:10px;font-weight:900;color:#d93025;text-transform:uppercase;letter-spacing:.5px}
.fsh-tools{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:12px}
.fsh-tools label{font-size:11px;font-weight:900;letter-spacing:1px;text-transform:uppercase;color:var(--mu)}
.fsh-tools select{padding:8px 10px;border:1px solid #ccc;border-radius:4px;font-family:'Lato';font-size:13px}
.fsh-tw{overflow-x:auto}
.fsh-t{width:100%;border-collapse:collapse;font-size:13px;min-width:760px}
.fsh-t th{background:var(--g);color:#fff;font-size:11px;letter-spacing:1px;text-transform:uppercase;padding:10px 8px;text-align:center;cursor:pointer;white-space:nowrap;user-select:none}
.fsh-t th.l,.fsh-t td.l{text-align:left}
.fsh-t th.on{color:var(--gd)}
.fsh-t th.on:after{content:" ▾"}.fsh-t th.on.asc:after{content:" ▴"}
.fsh-t td{padding:9px 8px;text-align:center;border-bottom:1px solid #eee}
.fsh-t tr:nth-child(even) td{background:#f9f9f9}
.fsh-t td a{font-weight:900;color:var(--ink);text-decoration:none}
.fsh-t td.z{color:#bbb}
.fsh-t.norate th:last-child,.fsh-t.norate td:last-child{display:none}
.fsh-t.noast th:nth-child(10),.fsh-t.noast td:nth-child(10),.fsh-t.noast th:nth-child(11),.fsh-t.noast td:nth-child(11){display:none}
.fsh-t td.best{background:rgba(227,197,103,.35)!important;font-weight:900}
.fsh-chart{padding:20px}
.fsh-chart h4{margin:0 0 4px;font-size:13px;font-weight:900;letter-spacing:1px;text-transform:uppercase;color:var(--ink)}
.fsh-chart .k{font-size:11px;color:var(--mu);margin-bottom:14px;display:flex;gap:14px}
.fsh-chart .k i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:5px;vertical-align:-1px}
.fsh-bars{display:flex;gap:10px;align-items:flex-end;height:190px;border-bottom:2px solid var(--ink);padding-top:16px}
.fsh-bc{flex:1;display:flex;flex-direction:column;align-items:center;height:100%;justify-content:flex-end}
.fsh-bp{display:flex;gap:3px;align-items:flex-end;height:100%;width:100%;justify-content:center}
.fsh-b{width:44%;max-width:26px;border-radius:3px 3px 0 0;position:relative}
.fsh-b span{position:absolute;top:-16px;left:50%;transform:translateX(-50%);font-size:10px;font-weight:900;color:var(--ink);white-space:nowrap}
.fsh-bl{font-size:10px;color:var(--mu);margin-top:6px;text-align:center;line-height:1.2;height:26px}
.fsh-hb{display:grid;grid-template-columns:140px 1fr 44px;gap:10px;align-items:center;margin-bottom:8px;font-size:12px}
.fsh-hb .n{font-weight:900;color:var(--ink);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.fsh-hb .tr{background:#f0f0ee;border-radius:3px;height:16px;overflow:hidden;display:flex}
.fsh-hb .tr i{display:block;height:100%}
.fsh-hb .v{font-weight:900;text-align:right}
.fsh-tile{padding:16px 12px;text-align:center;border-bottom:4px solid var(--gd)}
.fsh-tile b{display:block;font-family:'Playfair Display',serif;font-weight:900;font-size:30px;color:var(--g)}
.fsh-tile span{font-size:10px;letter-spacing:1px;text-transform:uppercase;color:var(--mu);font-weight:900}
.fsh-mx{padding:0;overflow:hidden}
.fsh-mx h4{margin:0;padding:12px 16px;background:var(--g);color:#fff;font-size:12px;letter-spacing:1.5px;text-transform:uppercase;font-weight:900;border-bottom:3px solid var(--gd)}
.fsh-mr{display:grid;grid-template-columns:64px 1fr auto;gap:10px;align-items:center;padding:11px 16px;border-bottom:1px solid #eee;text-decoration:none}
.fsh-mr:hover{background:#fafaf7}
.fsh-mr .dt{font-size:11px;color:var(--mu);line-height:1.3}
.fsh-mr .dt b{display:block;color:var(--ink);font-size:12px}
.fsh-mr .op{font-weight:900;color:var(--ink);font-size:14px;display:flex;align-items:center;gap:8px}
.fsh-mr .op img{width:22px;height:22px;object-fit:contain}
.fsh-mr .op em{font-style:normal;font-size:10px;font-weight:900;color:var(--mu);border:1px solid #ddd;border-radius:3px;padding:1px 4px}
.fsh-mr .cm{font-size:11px;color:var(--mu);margin-top:2px;font-weight:400}
.fsh-mr .rs{font-weight:900;font-size:13px;padding:5px 9px;border-radius:4px;min-width:58px;text-align:center}
.fsh-mr .ko{background:#f0f0ee;color:var(--ink)}
.fsh-more{display:block;text-align:center;padding:10px;font-size:11px;font-weight:900;letter-spacing:1px;text-transform:uppercase;color:var(--g);cursor:pointer;background:#fafaf7;border:0;width:100%}
.fsh-tbl{width:100%;border-collapse:collapse;font-size:13px}
.fsh-tbl th{background:var(--g);color:#fff;font-size:11px;text-transform:uppercase;letter-spacing:1px;padding:9px 6px}
.fsh-tbl td{padding:8px 6px;text-align:center;border-bottom:1px solid #eee}
.fsh-tbl td.l{text-align:left;font-weight:700}
.fsh-tbl tr.me td{background:rgba(227,197,103,.35);font-weight:900}
.fsh-info dl{display:grid;grid-template-columns:130px 1fr;margin:0;padding:6px 18px 14px}
.fsh-info dt,.fsh-info dd{padding:10px 0;border-bottom:1px solid #eee;margin:0;font-size:13px}
.fsh-info dt{font-size:10px;letter-spacing:1.5px;text-transform:uppercase;color:var(--mu);font-weight:900;padding-top:12px}
.fsh-info dd{font-weight:700;color:var(--ink)}
.fsh-cta{background:var(--g);color:#fff;border-radius:8px;padding:30px;display:flex;justify-content:space-between;align-items:center;gap:20px;border-left:8px solid var(--gd);margin:36px 0 10px}
.fsh-cta h3{font-family:'Playfair Display',serif;font-weight:900;text-transform:uppercase;font-size:26px;margin:0 0 6px;color:#fff}
.fsh-cta p{margin:0;opacity:.9;font-size:14px;max-width:620px}
.fsh-btn{background:var(--gd);color:var(--g)!important;font-weight:900;text-transform:uppercase;letter-spacing:1px;font-size:13px;padding:14px 22px;border-radius:5px;text-decoration:none;white-space:nowrap}
.fsh-src{font-size:11px;color:var(--mu);padding:18px 0 40px;border-top:1px solid #eee;margin-top:26px}
@media (max-width:900px){
 .fsh-ele{position:absolute;right:-30px;bottom:-40px;height:300px;opacity:.09;pointer-events:none}
.fsh-hero-in{position:relative;z-index:1;grid-template-columns:auto 1fr;padding:30px 16px 38px}
 .fsh-pos{grid-column:1/-1;text-align:left;display:flex;align-items:center;gap:18px;flex-wrap:wrap}
 .fsh-pos .n{font-size:54px}.fsh-pos .l{margin-top:4px}.fsh-form{justify-content:flex-start;margin:0}
 .fsh-h1{font-size:38px}.fsh-crest{width:84px;height:84px}.fsh-crest img{width:60px;height:60px}
 .fsh-g2{grid-template-columns:1fr}.fsh-g4{grid-template-columns:1fr 1fr}.fsh-g6{grid-template-columns:repeat(3,1fr)}
 .fsh-cta{flex-direction:column;align-items:flex-start}
}
@media (max-width:560px){
 .fsh-wrap{padding:0 16px}.fsh-h1{font-size:32px}.fsh-g4{grid-template-columns:1fr}
 .fsh-g6{grid-template-columns:1fr 1fr}.fsh-hb{grid-template-columns:104px 1fr 38px}
 .fsh-vs .t{font-size:14px}.fsh-bars{gap:5px}.fsh-bl{font-size:9px}
}
</style>

<section class="fsh-hero">
 <img class="fsh-ele" src="https://footballsiam.com/wp-content/uploads/2026/10/fs-erawan-gold.png" alt="">
 <div class="fsh-hero-in">
  <div class="fsh-crest"><?php if ( $d['logo'] ) : ?><img src="<?php echo $e( $d['logo'] ); ?>" alt="<?php echo $e( $d['name'] ); ?>"><?php endif; ?></div>
  <div>
   <div class="fsh-kick"><?php echo $e( $league ); ?> · <?php echo $e( $season ); ?></div>
   <h1 class="fsh-h1"><?php echo $e( $d['name'] ); ?></h1>
   <div class="fsh-meta">
    <?php if ( $d['coach'] ) : ?><span><b>Head coach</b><?php echo $d['coach_url'] ? '<a href="' . $e( $d['coach_url'] ) . '" style="color:#fff">' . $e( $d['coach'] ) . '</a>' : $e( $d['coach'] ); ?></span><?php endif; ?>
    <?php if ( $d['stadium'] ) : ?><span><b>Stadium</b><?php echo $e( $d['stadium'] ); ?><?php echo $d['capacity'] ? ' (' . $e( number_format( (int) $d['capacity'] ) ) . ')' : ''; ?></span><?php endif; ?>
    <?php if ( $d['city'] ) : ?><span><b>City</b><?php echo $e( $d['city'] ); ?></span><?php endif; ?>
    <?php if ( $d['nickname'] ) : ?><span><b>Nickname</b><?php echo $e( $d['nickname'] ); ?></span><?php endif; ?>
   </div>
  </div>
  <div class="fsh-pos">
   <?php if ( $me ) : ?>
   <div><div class="n"><?php echo $e( $ord( $me['pos'] ) ); ?></div><div class="l">in <?php echo $e( $league ); ?></div>
   <div class="pts"><b><?php echo (int) $me['pts']; ?></b> pts · <?php echo (int) $me['p']; ?> played · GD <?php echo ( $me['gd'] > 0 ? '+' : '' ) . (int) $me['gd']; ?></div></div>
   <?php endif; ?>
   <div class="fsh-form" title="Last five, all competitions">
    <?php foreach ( array_reverse( $form ) as $r ) : ?>
     <a class="fsh-fp fsh-<?php echo $e( $r['res'] ); ?>" href="<?php echo $e( $r['url'] ); ?>" title="<?php echo $e( ( $r['home'] ? 'v ' : 'at ' ) . $r['opp'] . ' ' . $r['hs'] . '-' . $r['as'] . ', ' . fs_hub_comp_short( $r['comp'] ) ); ?>"><?php echo $e( $r['res'] ); ?></a>
    <?php endforeach; ?>
   </div>
  </div>
 </div>
</section>

<nav class="fsh-nav"><div class="fsh-wrap">
 <a href="#fsh-overview">Overview</a><a href="#fsh-squad">Squad</a><a href="#fsh-stats">Player stats</a>
 <a href="#fsh-team">Team stats</a><a href="#fsh-matches">Fixtures &amp; results</a><a href="#fsh-table">Table</a><a href="#fsh-club">Club</a>
</div></nav>

<div class="fsh-wrap">

<section class="fsh-sec" id="fsh-overview">
 <h2 class="fsh-h2">Overview</h2><div class="fsh-rule"></div>
 <div class="fsh-g2">
  <?php foreach ( array( array( 'Next match', $next ), array( 'Last result', $last ) ) as $mm ) : list( $lbl, $m ) = $mm; if ( ! $m ) continue;
	$us = '<span class="t' . ( $m['home'] ? '' : ' r' ) . '">'; ?>
  <div class="fsh-card fsh-match">
   <div class="lbl"><?php echo $e( $lbl ); ?></div>
   <div class="cmp"><?php echo $e( fs_hub_comp_short( $m['comp'] ) . ( $m['round'] ? ' · ' . $m['round'] : '' ) ); ?></div>
   <div class="fsh-vs">
    <?php $hn = $m['home'] ? $d['name'] : $m['opp']; $an = $m['home'] ? $m['opp'] : $d['name'];
	  $hl = $m['home'] ? $d['logo'] : $m['opp_logo']; $al = $m['home'] ? $m['opp_logo'] : $d['logo']; ?>
    <div class="t"><?php if ( $hl ) echo '<img src="' . $e( $hl ) . '" alt="">'; echo $e( $hn ); ?></div>
    <a class="fsh-sc" href="<?php echo $e( $m['url'] ); ?>"><?php echo $lbl === 'Next match' ? $e( $m['time'] ) : $e( $m['hs'] . ' - ' . $m['as'] ); ?></a>
    <div class="t r"><?php echo $e( $an ); if ( $al ) echo '<img src="' . $e( $al ) . '" alt="">'; ?></div>
   </div>
   <div class="foot"><?php echo $e( fs_hub_date( $m['ts'], 'l j F Y' ) ); ?><?php echo $lbl === 'Next match' ? ' · Bangkok time' : ''; ?></div>
  </div>
  <?php endforeach; ?>
 </div>
 <div class="fsh-card fsh-rec">
  <div><b><?php echo $rec['p']; ?></b><span>Played</span></div><div><b><?php echo $rec['w']; ?></b><span>Won</span></div>
  <div><b><?php echo $rec['d']; ?></b><span>Drawn</span></div><div><b><?php echo $rec['l']; ?></b><span>Lost</span></div>
  <div><b><?php echo $rec['gf']; ?></b><span>Scored</span></div><div><b><?php echo $rec['ga']; ?></b><span>Conceded</span></div>
 </div>
 <p class="fsh-sub" style="margin:8px 0 18px">All competitions, <?php echo $e( $season ); ?>.</p>
 <div class="fsh-g4">
  <?php foreach ( $leaders as $L ) : list( $k, $p, $key, $unit ) = $L; if ( ! $p ) continue; $v = $p['stats'][ $key ]; ?>
  <div class="fsh-card fsh-lead"><div class="v"><?php echo $e( $key === 'rating' ? number_format( $v, 2 ) : $v ); ?></div>
   <div><div class="k"><?php echo $e( $k ); ?></div><a class="p" href="<?php echo $e( $p['url'] ); ?>"><?php echo $e( $p['name'] ); ?></a><div class="u"><?php echo $e( $unit ); ?> · <?php echo (int) $p['stats']['apps']; ?> apps</div></div></div>
  <?php endforeach; ?>
 </div>
</section>

<section class="fsh-sec" id="fsh-squad">
 <h2 class="fsh-h2">The squad</h2><div class="fsh-rule"></div>
 <p class="fsh-sub"><?php echo count( $sq ); ?> registered players. Season figures are all competitions.</p>
 <?php foreach ( $groups as $gk => $gl ) : if ( empty( $by[ $gk ] ) ) continue; ?>
 <div class="fsh-grp"><h3><?php echo $e( $gl ); ?><span><?php echo count( $by[ $gk ] ); ?></span></h3>
  <div class="fsh-pl">
  <?php foreach ( $by[ $gk ] as $p ) : $s = $p['stats']; $r = $s ? $s['rating'] : null; ?>
   <a class="fsh-card fsh-p" href="<?php echo $e( $p['url'] ?: '#' ); ?>">
    <div class="fsh-no"><?php if ( $p['photo'] ) : ?><img src="<?php echo $e( $p['photo'] ); ?>" alt=""><i><?php echo $e( $p['number'] ); ?></i><?php else : echo $e( $p['number'] ); endif; ?></div>
    <div style="min-width:0">
     <div class="fsh-pn"><?php echo $e( $p['name'] ); ?></div>
     <?php if ( ! empty( $p['name_th'] ) ) : ?><div class="fsh-pth" lang="th"><?php echo $e( $p['name_th'] ); ?></div><?php endif; ?>
     <div class="fsh-pm"><?php echo $e( implode( ' · ', array_filter( array( $p['nat'], $p['age'] ? $p['age'] . ' yrs' : '', $p['height'] ? $p['height'] . ' cm' : '' ) ) ) ); ?></div>
     <?php if ( $p['injury'] ) : ?><div class="fsh-inj">Injured</div><?php endif; ?>
     <div class="fsh-ps"><?php if ( $s && $s['apps'] ) : ?><span><b><?php echo (int) $s['apps']; ?></b> apps</span><span><b><?php echo (int) $s['mins']; ?></b> mins</span><?php if ( $gk !== 'GK' ) : ?><span><b><?php echo (int) $s['goals']; ?></b> G</span><?php if ( $has_ast ) : ?><span><b><?php echo (int) $s['assists']; ?></b> A</span><?php endif; ?><?php endif; else : ?><span>No appearances yet</span><?php endif; ?></div>
    </div>
    <?php if ( $r ) : ?><div class="fsh-rt<?php echo $r >= 7.3 ? ' top' : ( $r >= 7 ? ' hi' : '' ); ?>"><?php echo $e( number_format( $r, 2 ) ); ?></div><?php endif; ?>
   </a>
  <?php endforeach; ?>
  </div></div>
 <?php endforeach; ?>
</section>

<section class="fsh-sec" id="fsh-stats">
 <h2 class="fsh-h2">Player stats</h2><div class="fsh-rule"></div>
 <div class="fsh-tools"><label for="fsh-comp">Competition</label>
  <select id="fsh-comp"><option value="">All competitions</option><?php foreach ( $comps as $c ) echo '<option value="' . $e( $c ) . '">' . $e( $c ) . '</option>'; ?></select>
  <span style="font-size:12px;color:var(--mu)">Click a column to sort.<?php echo $has_rating ? ' Ratings are FotMob match ratings, averaged.' : ''; ?></span></div>
 <div class="fsh-card fsh-tw"><table class="fsh-t<?php echo ( $has_rating ? '' : ' norate' ) . ( $has_ast ? '' : ' noast' ); ?>" id="fsh-t"><thead><tr>
  <th data-k="number">#</th><th class="l" data-k="name" data-s="1">Player</th><th data-k="posn">Pos</th><th class="l" data-k="nat" data-s="1">Nat</th><th data-k="age">Age</th>
  <th data-k="apps">Apps</th><th data-k="starts">Starts</th><th data-k="mins" class="on">Mins</th><th data-k="goals">Goals</th><th data-k="assists">Ast</th><th data-k="ga">G+A</th>
  <th data-k="yellow">YC</th><th data-k="red">RC</th><th data-k="rating">Rating</th></tr></thead><tbody></tbody></table></div>
</section>

<section class="fsh-sec" id="fsh-team">
 <h2 class="fsh-h2">Team stats</h2><div class="fsh-rule"></div>
 <?php if ( $has_tiles ) : ?>
 <p class="fsh-sub"><?php echo $e( $league ); ?>, per game, from <?php echo count( $lg ); ?> matches.</p>
 <div class="fsh-g6" style="margin-bottom:18px">
 <?php foreach ( $tiles as $T ) : if ( $T[1] === null ) continue; ?><div class="fsh-card fsh-tile"><b><?php echo $e( number_format( $T[1], $T[2] ) . $T[3] ); ?></b><span><?php echo $e( $T[0] ); ?></span></div><?php endforeach; ?>
 </div>
 <?php else : ?>
 <p class="fsh-sub"><?php echo $e( $season ); ?>, all competitions.</p>
 <?php endif; ?>
 <div class="fsh-g2">
  <?php if ( $has_xg ) : ?>
  <div class="fsh-card fsh-chart"><h4>Goals v expected goals, by league match</h4>
   <div class="k"><span><i style="background:#0A3B25"></i>Goals</span><span><i style="background:#e3c567"></i>xG</span></div>
   <?php $mx = 0.5; foreach ( $lg as $m ) $mx = max( $mx, (float) $m['gf'], (float) ( $m['stats']['Expected goals (xG)'] ?? 0 ) ); ?>
   <div class="fsh-bars"><?php foreach ( $lg as $m ) : $xg = (float) ( $m['stats']['Expected goals (xG)'] ?? 0 ); ?>
    <div class="fsh-bc"><div class="fsh-bp">
     <div class="fsh-b" style="height:<?php echo round( 100 * $m['gf'] / $mx ); ?>%;background:#0A3B25"><span><?php echo (int) $m['gf']; ?></span></div>
     <div class="fsh-b" style="height:<?php echo round( 100 * $xg / $mx ); ?>%;background:#e3c567"><span><?php echo $e( number_format( $xg, 1 ) ); ?></span></div>
    </div><div class="fsh-bl"><?php echo $e( ( $m['home'] ? '' : '@ ' ) . $m['opp'] ); ?></div></div>
   <?php endforeach; ?></div>
  </div>
  <?php endif; if ( $has_shots ) : ?>
  <div class="fsh-card fsh-chart"><h4>Shots and shots on target, by league match</h4>
   <div class="k"><span><i style="background:#0A3B25"></i>Shots</span><span><i style="background:#e3c567"></i>On target</span></div>
   <?php $mx = 1; foreach ( $lg as $m ) $mx = max( $mx, (float) ( $m['stats']['Total shots'] ?? 0 ) ); ?>
   <div class="fsh-bars"><?php foreach ( $lg as $m ) : $sh = (int) ( $m['stats']['Total shots'] ?? 0 ); $so = (int) ( $m['stats']['Shots on target'] ?? 0 ); ?>
    <div class="fsh-bc"><div class="fsh-bp">
     <div class="fsh-b" style="height:<?php echo round( 100 * $sh / $mx ); ?>%;background:#0A3B25"><span><?php echo $sh; ?></span></div>
     <div class="fsh-b" style="height:<?php echo round( 100 * $so / $mx ); ?>%;background:#e3c567"><span><?php echo $so; ?></span></div>
    </div><div class="fsh-bl"><?php echo $e( ( $m['home'] ? '' : '@ ' ) . $m['opp'] ); ?></div></div>
   <?php endforeach; ?></div>
  </div>
  <?php endif; ?>
  <?php
	$ranked = array_values( array_filter( $sq, function ( $p ) { return $p['stats'] && ( $p['stats']['goals'] + $p['stats']['assists'] ) > 0; } ) );
	usort( $ranked, function ( $a, $b ) { $x = $a['stats']['goals'] + $a['stats']['assists']; $y = $b['stats']['goals'] + $b['stats']['assists']; return $y - $x ?: $b['stats']['goals'] - $a['stats']['goals']; } );
	$ranked = array_slice( $ranked, 0, 10 ); $mx = $ranked ? $ranked[0]['stats']['goals'] + $ranked[0]['stats']['assists'] : 1;
  ?>
  <div class="fsh-card fsh-chart"><h4><?php echo $has_ast ? 'Goal involvements' : 'Goals'; ?></h4>
   <div class="k"><span><i style="background:#0A3B25"></i>Goals</span><span><i style="background:#e3c567"></i>Assists</span></div>
   <?php foreach ( $ranked as $p ) : $g = $p['stats']['goals']; $a = $p['stats']['assists']; ?>
    <div class="fsh-hb"><div class="n"><?php echo $e( $p['name'] ); ?></div><div class="tr"><i style="width:<?php echo 100 * $g / $mx; ?>%;background:#0A3B25"></i><i style="width:<?php echo 100 * $a / $mx; ?>%;background:#e3c567"></i></div><div class="v"><?php echo $g + $a; ?></div></div>
   <?php endforeach; ?>
  </div>
  <?php
	$mins = array_values( array_filter( $sq, function ( $p ) { return $p['stats'] && $p['stats']['mins'] > 0; } ) );
	usort( $mins, function ( $a, $b ) { return $b['stats']['mins'] - $a['stats']['mins']; } );
	$mins = array_slice( $mins, 0, 10 ); $poss = max( 1, 90 * ( $mt ? count( $mt ) : count( $res ) ) );
  ?>
  <div class="fsh-card fsh-chart"><h4>Share of minutes played</h4>
   <div class="k"><span><i style="background:#0A3B25"></i>Minutes, % of the <?php echo (int) $poss; ?> available</span></div>
   <?php foreach ( $mins as $p ) : $pc = min( 100, 100 * $p['stats']['mins'] / $poss ); ?>
    <div class="fsh-hb"><div class="n"><?php echo $e( $p['name'] ); ?></div><div class="tr"><i style="width:<?php echo $pc; ?>%;background:<?php echo $pc >= 75 ? '#0A3B25' : 'rgba(10,59,37,.55)'; ?>"></i></div><div class="v"><?php echo round( $pc ); ?>%</div></div>
   <?php endforeach; ?>
  </div>
 </div>
</section>

<section class="fsh-sec" id="fsh-matches">
 <h2 class="fsh-h2">Fixtures &amp; results</h2><div class="fsh-rule"></div>
 <div class="fsh-g2">
  <?php foreach ( array( array( 'Fixtures', $fix, false ), array( 'Results', $res, true ) ) as $blk ) : list( $t, $rows, $isres ) = $blk; ?>
  <div class="fsh-card fsh-mx"><h4><?php echo $e( $t ); ?></h4>
   <?php if ( ! $rows ) echo '<div style="padding:16px;color:#888;font-size:13px">None yet.</div>';
	foreach ( $rows as $i => $m ) : ?>
   <a class="fsh-mr" href="<?php echo $e( $m['url'] ); ?>"<?php echo $i >= 8 ? ' data-more="1" style="display:none"' : ''; ?>>
    <div class="dt"><b><?php echo $e( fs_hub_date( $m['ts'], 'j M' ) ); ?></b><?php echo $e( fs_hub_date( $m['ts'], 'D' ) ); ?></div>
    <div><div class="op"><?php if ( $m['opp_logo'] ) echo '<img src="' . $e( $m['opp_logo'] ) . '" alt="">'; ?><?php echo $e( $m['opp'] ); ?> <em><?php echo $m['home'] ? 'H' : 'A'; ?></em></div>
     <div class="cm"><?php echo $e( fs_hub_comp_short( $m['comp'] ) . ( $m['round'] ? ' · ' . $m['round'] : '' ) ); ?></div></div>
    <?php if ( $isres ) : $gf = $m['home'] ? $m['hs'] : $m['as']; $ga = $m['home'] ? $m['as'] : $m['hs']; ?>
     <div class="rs fsh-<?php echo $e( $m['res'] ); ?>"><?php echo $e( $m['res'] . ' ' . $gf . '-' . $ga ); ?></div>
    <?php else : ?><div class="rs ko"><?php echo $e( $m['time'] ); ?></div><?php endif; ?>
   </a>
   <?php endforeach; if ( count( $rows ) > 8 ) : ?><button type="button" class="fsh-more">Show all <?php echo count( $rows ); ?></button><?php endif; ?>
  </div>
  <?php endforeach; ?>
 </div>
</section>

<section class="fsh-sec" id="fsh-table">
 <h2 class="fsh-h2"><?php echo $e( $league ); ?> table</h2><div class="fsh-rule"></div>
 <?php if ( $d['table'] ) : ?>
 <div class="fsh-card" style="overflow:hidden"><table class="fsh-tbl"><thead><tr><th>Pos</th><th style="text-align:left">Club</th><th>P</th><th>W</th><th>D</th><th>L</th><th>GD</th><th>Pts</th></tr></thead><tbody>
 <?php foreach ( $d['table'] as $r ) : ?>
  <tr class="<?php echo $r['me'] ? 'me' : ''; ?>"><td><?php echo (int) $r['pos']; ?></td><td class="l"><?php echo $r['id'] ? '<a href="' . $e( get_permalink( $r['id'] ) ) . '" style="text-decoration:none">' . $e( $r['name'] ) . '</a>' : $e( $r['name'] ); ?></td>
  <td><?php echo (int) $r['p']; ?></td><td><?php echo (int) $r['w']; ?></td><td><?php echo (int) $r['d']; ?></td><td><?php echo (int) $r['l']; ?></td><td><?php echo ( $r['gd'] > 0 ? '+' : '' ) . (int) $r['gd']; ?></td><td><b><?php echo (int) $r['pts']; ?></b></td></tr>
 <?php endforeach; ?></tbody></table></div>
 <?php endif; ?>
</section>

<section class="fsh-sec" id="fsh-club">
 <h2 class="fsh-h2">The club</h2><div class="fsh-rule"></div>
 <div class="fsh-g2">
  <div class="fsh-card fsh-info"><dl>
   <?php foreach ( array( 'Founded' => $d['founded'], 'Stadium' => $d['stadium'], 'Capacity' => $d['capacity'] ? number_format( (int) $d['capacity'] ) : '', 'City' => $d['city'], 'Head coach' => $d['coach'], 'Owner' => $d['owner'], 'Last season' => $d['last_finish'] ) as $k => $v ) : if ( ! $v ) continue; ?>
    <dt><?php echo $e( $k ); ?></dt><dd><?php echo $e( $v ); ?></dd>
   <?php endforeach; ?>
  </dl></div>
  <?php $q = rawurlencode( trim( ( $d['stadium'] ?: $d['name'] ) . ( $d['city'] ? ', ' . $d['city'] : '' ) ) ); ?>
  <div class="fsh-card" style="overflow:hidden;min-height:300px"><iframe title="Stadium map" width="100%" height="320" loading="lazy" style="border:0;display:block" src="https://maps.google.com/maps?q=<?php echo $q; ?>&t=m&z=15&output=embed"></iframe></div>
 </div>
 <div class="fsh-src"><?php echo ( isset( $hub['source'] ) && $hub['source'] === 'Thai League' ) ? 'Squad, photos and player stats: Thai League registration' : 'Squad, player and team stats: FotMob'; ?>, updated <?php echo $e( $upd ); ?> (Bangkok). Results, fixtures and table: Football Siam.</div>
</section>
</div>

<script>
(function(){
 var SQ=<?php
	$js = array();
	$pn = array( 'GK' => 1, 'DF' => 2, 'MF' => 3, 'FW' => 4 );
	foreach ( $sq as $p ) $js[] = array( 'number' => (int) $p['number'], 'name' => $p['name'], 'url' => $p['url'], 'pos' => $p['pos'], 'posn' => $pn[ $p['pos'] ] ?? 5, 'nat' => $p['nat'], 'age' => (int) $p['age'], 'total' => $p['stats'], 'by' => $p['by_comp'] );
	echo wp_json_encode( $js );
 ?>;
 var tb=document.querySelector('#fsh-t tbody'),sel=document.getElementById('fsh-comp'),ths=document.querySelectorAll('#fsh-t th'),key='mins',asc=false;
 var NUM=['apps','starts','mins','goals','assists','ga','yellow','red','rating'];
 function row(p){var s=sel.value?(p.by&&p.by[sel.value]):p.total;s=s||{};var o={number:p.number,name:p.name,url:p.url,pos:p.pos,posn:p.posn,nat:p.nat,age:p.age};
  NUM.forEach(function(k){o[k]=k==='ga'?((s.goals||0)+(s.assists||0)):(s[k]==null?(k==='rating'?null:0):s[k]);});return o;}
 function draw(){var rows=SQ.map(row);rows.sort(function(a,b){var x=a[key],y=b[key];
   if(key==='name'||key==='nat'){x=String(x||'');y=String(y||'');return asc?x.localeCompare(y):y.localeCompare(x);}
   x=x==null?-1:x;y=y==null?-1:y;return asc?x-y:(y-x)||(b.mins-a.mins);});
  var best={};NUM.forEach(function(k){best[k]=Math.max.apply(null,rows.map(function(r){return r[k]||0;}));});
  tb.innerHTML=rows.map(function(r){return '<tr><td>'+(r.number||'')+'</td><td class="l"><a href="'+(r.url||'#')+'">'+esc(r.name)+'</a></td><td>'+r.pos+'</td><td class="l">'+esc(r.nat)+'</td><td>'+(r.age||'')+'</td>'+
   NUM.map(function(k){var v=r[k];var z=(v===0||v==null);var b=(!z&&v===best[k]&&k!=='yellow'&&k!=='red');return '<td class="'+(z?'z':'')+(b?' best':'')+'">'+(v==null?'–':(k==='rating'?v.toFixed(2):v))+'</td>';}).join('')+'</tr>';}).join('');}
 function esc(s){return String(s||'').replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];});}
 ths.forEach(function(th){th.addEventListener('click',function(){var k=th.getAttribute('data-k');if(k===key)asc=!asc;else{key=k;asc=!!th.getAttribute('data-s')||k==='number'||k==='posn';}
  ths.forEach(function(t){t.classList.remove('on','asc');});th.classList.add('on');if(asc)th.classList.add('asc');draw();});});
 sel.addEventListener('change',draw);draw();
 document.querySelectorAll('.fsh-more').forEach(function(b){b.addEventListener('click',function(){b.parentNode.querySelectorAll('[data-more]').forEach(function(r){r.style.display='';});b.remove();});});
 var links=document.querySelectorAll('.fsh-nav a'),secs=[].map.call(links,function(a){return document.querySelector(a.getAttribute('href'));});
 window.addEventListener('scroll',function(){var y=window.scrollY+90,on=0;secs.forEach(function(s,i){if(s&&s.offsetTop<=y)on=i;});links.forEach(function(a,i){a.classList.toggle('on',i===on);});},{passive:true});
})();
</script>
</div>
	<?php
	return ob_get_clean();
}

/* ------------------------------------------------------------------------
 * Page takeover for enabled clubs
 * --------------------------------------------------------------------- */

// template_include (not template_redirect) so Divi's Theme Builder and
// dynamic assets have already set up: the site header/footer then render
// with their own styles.
add_filter( 'template_include', function ( $tpl ) {
	if ( is_admin() || ! is_singular( 'club' ) ) return $tpl;
	$cid = get_queried_object_id();
	$hubraw = (string) get_post_meta( $cid, 'fs_club_hub_json', true );
	// ASEAN top-flight clubs (24 Oct 2026) switch on once their FotMob hub JSON exists
	if ( ! in_array( (int) $cid, fs_club_hub_enabled(), true ) && strpos( $hubraw, '"source": "Thai League"' ) === false
		&& ! preg_match( '/"league_slug": "(indonesian-super-league|malaysia-super-league|singapore-premier-league|v-league-1)"/', $hubraw ) ) return $tpl;
	if ( isset( $_GET['fs_oldclub'] ) ) return $tpl; // ?fs_oldclub=1 shows the previous page
	$d = fs_hub_data( $cid );
	if ( ! $d ) return $tpl; // no FotMob feed yet: keep the old page
	get_header();
	echo fs_hub_render( $d );
	get_footer();
	exit;
}, 999 );

}
