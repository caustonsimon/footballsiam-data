<?php
/**
 * FS ASEAN top flights: tables + fixture/result filters (added 2026-10-24)
 *
 * Indonesia, Malaysia, Singapore and Vietnam joined the match sync on
 * 24 Oct 2026 (competition terms 254-257, club pages 41224-41275). This adds
 * them to the existing pages without touching the older engine snippets:
 *   - Fixtures / Results: one optgroup per country in the Competition filter.
 *   - Tables page: one optgroup per country in the league selector, plus a
 *     2026/27 table for each league, built here from the league's matches by
 *     club post id (same markup and classes as the existing tables).
 * Works by filtering the shortcodes' output (do_shortcode_tag), so it keeps
 * working whichever engine snippet renders the page.
 */

if ( ! function_exists( 'fs_asean_leagues' ) ) {
function fs_asean_leagues() {
	return array(
		'Indonesia' => array( 'indonesian-super-league' => 'Indonesian Super League' ),
		'Malaysia'  => array( 'malaysia-super-league' => 'Malaysia Super League' ),
		'Singapore' => array( 'singapore-premier-league' => 'Singapore Premier League' ),
		'Vietnam'   => array( 'v-league-1' => 'V.League 1' ),
	);
}

function fs_asean_optgroups( $selected = '' ) {
	$h = '';
	foreach ( fs_asean_leagues() as $country => $lgs ) {
		$h .= '<optgroup label="' . esc_attr( $country ) . '">';
		foreach ( $lgs as $slug => $name ) $h .= '<option value="' . esc_attr( $slug ) . '"' . selected( $selected, $slug, false ) . '>' . esc_html( $name ) . '</option>';
		$h .= "</optgroup>\n    ";
	}
	return $h;
}

/** Standings for one league, 2026/27, by club post id. Cached 30 minutes. */
function fs_asean_table_rows( $slug ) {
	$key = 'fs_asean_tbl_' . md5( $slug );
	$c = get_transient( $key );
	if ( is_array( $c ) ) return $c;
	$ids = get_posts( array(
		'post_type' => 'matches', 'post_status' => 'publish', 'posts_per_page' => -1, 'fields' => 'ids', 'no_found_rows' => true,
		'tax_query'  => array( array( 'taxonomy' => 'competition', 'field' => 'slug', 'terms' => array( $slug ) ) ),
		'meta_query' => array( array( 'key' => 'season', 'value' => array( '2026/2027', '2026/27' ), 'compare' => 'IN' ) ),
	) );
	$t = array(); $games = array();
	foreach ( $ids as $pid ) {
		$h = (int) get_post_meta( $pid, 'home_team', true ); $a = (int) get_post_meta( $pid, 'away_team', true );
		if ( ! $h || ! $a ) continue;
		foreach ( array( $h, $a ) as $cid ) if ( ! isset( $t[ $cid ] ) ) $t[ $cid ] = array( 'id' => $cid, 'p' => 0, 'w' => 0, 'd' => 0, 'l' => 0, 'gf' => 0, 'ga' => 0, 'pts' => 0, 'form' => array() );
		$hs = get_post_meta( $pid, 'home_score', true ); $as = get_post_meta( $pid, 'away_score', true );
		if ( $hs === '' || $hs === null || $hs === false || $as === '' || $as === null || $as === false ) continue;
		$games[] = array( (string) get_post_meta( $pid, 'date', true ) . (string) get_post_meta( $pid, 'kick_off_time', true ), $h, $a, (int) $hs, (int) $as );
	}
	usort( $games, function ( $x, $y ) { return strcmp( $x[0], $y[0] ); } );
	foreach ( $games as $g ) {
		foreach ( array( array( $g[1], $g[3], $g[4] ), array( $g[2], $g[4], $g[3] ) ) as $x ) {
			$r = &$t[ $x[0] ];
			$r['p']++; $r['gf'] += $x[1]; $r['ga'] += $x[2];
			if ( $x[1] > $x[2] ) { $r['w']++; $r['pts'] += 3; $r['form'][] = 'w'; }
			elseif ( $x[1] === $x[2] ) { $r['d']++; $r['pts']++; $r['form'][] = 'd'; }
			else { $r['l']++; $r['form'][] = 'l'; }
			unset( $r );
		}
	}
	$t = array_values( $t );
	usort( $t, function ( $a, $b ) {
		if ( $a['pts'] !== $b['pts'] ) return $b['pts'] - $a['pts'];
		$gda = $a['gf'] - $a['ga']; $gdb = $b['gf'] - $b['ga'];
		if ( $gda !== $gdb ) return $gdb - $gda;
		if ( $a['gf'] !== $b['gf'] ) return $b['gf'] - $a['gf'];
		return strcasecmp( get_the_title( $a['id'] ), get_the_title( $b['id'] ) );
	} );
	set_transient( $key, $t, 30 * MINUTE_IN_SECONDS );
	return $t;
}

function fs_asean_table_html( $slug, $name ) {
	$rows = fs_asean_table_rows( $slug );
	$h  = '<div id="table-' . esc_attr( $slug ) . '-20262027" class="fs-league-table-container" style="display: none;">';
	$h .= '<div style="color:#0A3B25; font-weight:900; font-size:14px; margin-bottom:10px; text-transform:uppercase;">' . esc_html( $name ) . ' — 2026/27</div>';
	$h .= '<div style="overflow-x:auto;"><table class="fs-table"><thead><tr><th class="fs-pos-col">#</th><th class="fs-team-col">Club</th><th title="Played">P</th><th title="Won">W</th><th title="Drawn">D</th><th title="Lost">L</th><th title="Goals For">F</th><th title="Goals Against">A</th><th title="Goal Difference">GD</th><th title="Points">Pts</th><th>Form</th></tr></thead><tbody>';
	foreach ( $rows as $i => $r ) {
		$logo = function_exists( 'get_field' ) ? (string) get_field( 'club_logo_url', $r['id'] ) : '';
		if ( $logo === '' ) $logo = (string) get_the_post_thumbnail_url( $r['id'], 'thumbnail' );
		$gd = $r['gf'] - $r['ga'];
		$form = '';
		foreach ( array_slice( $r['form'], -5 ) as $f ) $form .= '<span class="fs-form-dot fs-form-' . $f . '" title="' . strtoupper( $f ) . '"></span>';
		$h .= '<tr><td class="fs-pos-col">' . ( $i + 1 ) . '</td><td class="fs-team-col"><a href="' . esc_url( get_permalink( $r['id'] ) ) . '">'
			. ( $logo ? '<img decoding="async" src="' . esc_url( $logo ) . '" class="fs-tbl-logo" alt="">' : '' ) . esc_html( get_the_title( $r['id'] ) ) . '</a></td>'
			. '<td>' . $r['p'] . '</td><td>' . $r['w'] . '</td><td>' . $r['d'] . '</td><td>' . $r['l'] . '</td><td>' . $r['gf'] . '</td><td>' . $r['ga'] . '</td>'
			. '<td>' . ( $gd > 0 ? '+' . $gd : $gd ) . '</td><td class="fs-pts-col">' . $r['pts'] . '</td><td><div class="fs-form-guide">' . $form . '</div></td></tr>';
	}
	if ( ! $rows ) $h .= '<tr><td colspan="11">No matches yet.</td></tr>';
	$h .= '</tbody></table></div></div>';
	// 2025/26 was not imported for these leagues
	$h .= '<div id="table-' . esc_attr( $slug ) . '-20252026" class="fs-league-table-container" style="display: none;"><p style="font-family:Lato,sans-serif;">' . esc_html( $name ) . ': 2026/27 is the first season on Football Siam.</p></div>';
	return $h;
}

add_filter( 'do_shortcode_tag', function ( $out, $tag ) {
	if ( ! is_string( $out ) ) return $out;
	if ( in_array( $tag, array( 'fs_results', 'fs_fixtures', 'fs_guardian_matches' ), true ) ) {
		$anchor = '<optgroup label="Continental & International">';
		if ( strpos( $out, $anchor ) !== false && strpos( $out, 'value="v-league-1"' ) === false ) {
			$sel = isset( $_GET['fs_comp'] ) ? sanitize_text_field( wp_unslash( $_GET['fs_comp'] ) ) : '';
			$out = str_replace( $anchor, fs_asean_optgroups( $sel ) . $anchor, $out );
		}
		return $out;
	}
	// any shortcode that prints the tables-page selector (Divi wraps it, so match the markup, not the tag)
	if ( strpos( $out, 'id="fs-table-selector"' ) !== false && strpos( $out, 'value="v-league-1"' ) === false ) {
		$anchor = '<optgroup label="ASEAN">';
		if ( strpos( $out, $anchor ) === false ) $anchor = '</select>';
		$pos = strpos( $out, $anchor, strpos( $out, 'id="fs-table-selector"' ) );
		if ( $pos !== false ) $out = substr( $out, 0, $pos ) . fs_asean_optgroups() . substr( $out, $pos );
		$tables = '';
		foreach ( fs_asean_leagues() as $lgs ) foreach ( $lgs as $slug => $name ) $tables .= fs_asean_table_html( $slug, $name );
		$s = strpos( $out, '<script>' );
		$out = $s !== false ? substr( $out, 0, $s ) . $tables . substr( $out, $s ) : $out . $tables;
	}
	return $out;
}, 20, 2 );

// fresh tables after each sync write
add_action( 'updated_post_meta', function ( $mid, $pid, $key ) {
	if ( in_array( $key, array( 'home_score', 'away_score' ), true ) && get_post_type( $pid ) === 'matches' )
		foreach ( fs_asean_leagues() as $lgs ) foreach ( $lgs as $slug => $n ) delete_transient( 'fs_asean_tbl_' . md5( $slug ) );
}, 10, 3 );
}
