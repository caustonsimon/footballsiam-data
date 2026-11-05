<?php
/**
 * FS Cup round labels v2  (added 2026-11-05, replaces "FS Thai FA Cup round labels" v1)
 *
 * FootyStats has no match weeks for the cups, so the sync stores the ROUND
 * NUMBER in match_week for every cup match. This snippet turns those numbers
 * into round names for both domestic cups:
 *
 *   thai-fa-cup:     0 Qualifying Round, 1 First Round, 2 Second Round,
 *                    3 Third Round, 4 Quarter-final, 5 Semi-final, 6 Final
 *   thai-league-cup: 0 First qualification round, 1 Second qualification
 *                    round, 2 Qualification play-off round, 3 First round,
 *                    4 Second round, 5 Quarter-final, 6 Semi-final, 7 Final
 *
 * Every fixtures/results list on the site (Fixtures, Results, homepage, club
 * pages) prints "MW n", and the single match page prints "Match Week n".
 * For cup matches only, this swaps those for the round name. It works on the
 * finished page HTML, so the render snippets (146 / 156-158 / 179) are not
 * touched: each list row carries the match's own permalink, and only rows
 * whose permalink belongs to a cup post change. League rows are left alone.
 *
 * Club Hub rows (Snippet 196) are fixed the same way, so the hub itself is
 * unchanged. Keeps fs_facup_round_label() so the hub's existing call works,
 * and adds fs_cup_round_label( $slug, $n, $short ) for any cup.
 * Safe to have active alongside v1 for a moment while swapping over, but
 * deactivate and delete v1 once this is active.
 *
 * Code Snippets: scope "Only run on site front-end".
 */

if ( ! function_exists( 'fs_cup_round_label' ) ) {

	/** Cup slug => [ short labels, full labels ]. */
	function fs_cup_round_labels() {
		return array(
			'thai-fa-cup'     => array(
				array( 'Qual. Rd', 'Round 1', 'Round 2', 'Round 3', 'QF', 'SF', 'Final' ),
				array( 'Qualifying Round', 'First Round', 'Second Round', 'Third Round', 'Quarter-final', 'Semi-final', 'Final' ),
			),
			'thai-league-cup' => array(
				array( 'Qual. 1', 'Qual. 2', 'Play-off', 'Round 1', 'Round 2', 'QF', 'SF', 'Final' ),
				array( 'First qualification round', 'Second qualification round', 'Qualification play-off round', 'First round', 'Second round', 'Quarter-final', 'Semi-final', 'Final' ),
			),
		);
	}

	function fs_cup_round_label( $slug, $n, $short = true ) {
		$all = fs_cup_round_labels();
		$n   = (int) $n;
		if ( ! isset( $all[ $slug ] ) ) {
			return 'Round ' . $n;
		}
		$l = $all[ $slug ][ $short ? 0 : 1 ];
		return isset( $l[ $n ] ) ? $l[ $n ] : 'Round ' . $n;
	}

	if ( ! function_exists( 'fs_facup_round_label' ) ) {
		function fs_facup_round_label( $n, $short = true ) {
			return fs_cup_round_label( 'thai-fa-cup', $n, $short );
		}
	}

	/** permalink => [ slug, round ], for every cup match. Cached for an hour. */
	function fs_cup_round_map() {
		static $map = null;
		if ( $map !== null ) {
			return $map;
		}
		$map = get_transient( 'fs_cup_round_map_v2' );
		if ( is_array( $map ) ) {
			return $map;
		}
		$map = array();
		foreach ( array_keys( fs_cup_round_labels() ) as $slug ) {
			$ids = get_posts( array(
				'post_type'      => 'matches',
				'post_status'    => 'publish',
				'posts_per_page' => -1,
				'fields'         => 'ids',
				'no_found_rows'  => true,
				'tax_query'      => array( array(
					'taxonomy' => 'competition',
					'field'    => 'slug',
					'terms'    => array( $slug ),
				) ),
			) );
			foreach ( $ids as $id ) {
				$map[ untrailingslashit( get_permalink( $id ) ) ] = array( $slug, (int) get_post_meta( $id, 'match_week', true ) );
			}
		}
		set_transient( 'fs_cup_round_map_v2', $map, HOUR_IN_SECONDS );
		return $map;
	}

	/** New or updated match: rebuild the map on the next page view. */
	add_action( 'save_post_matches', function () {
		delete_transient( 'fs_cup_round_map_v2' );
	} );
	add_action( 'updated_post_meta', function ( $mid, $post_id, $key ) {
		if ( $key === 'match_week' && get_post_type( $post_id ) === 'matches' ) {
			delete_transient( 'fs_cup_round_map_v2' );
		}
	}, 10, 3 );

	function fs_cup_filter_html( $html ) {
		if ( ! is_string( $html ) || strpos( $html, '/matches/' ) === false ) {
			return $html;
		}
		$map = fs_cup_round_map();
		if ( empty( $map ) ) {
			return $html;
		}

		// 1. Single cup match page: "Match Week n" -> round name.
		if ( is_singular( 'matches' ) ) {
			$self = untrailingslashit( get_permalink( get_queried_object_id() ) );
			if ( isset( $map[ $self ] ) ) {
				$label = fs_cup_round_label( $map[ $self ][0], $map[ $self ][1], false );
				$html  = preg_replace( '/Match Week\s+\d+/', esc_html( $label ), $html, 1 );
			}
		}

		// 2. Club Hub fixture/result rows (Snippet 196): <a class="fsh-mr"
		//    href="match">...<div class="cm">League Cup · Matchday 2</div>.
		//    The hub only knows FA Cup rounds, so for every cup row the text
		//    after the competition name is set to the round name here.
		if ( strpos( $html, 'class="fsh-mr"' ) !== false ) {
			$rows = preg_split( '/(?=<a class="fsh-mr")/', $html );
			foreach ( $rows as $i => $seg ) {
				if ( $i === 0 || ! preg_match( '#^<a class="fsh-mr" href="([^"]+)"#', $seg, $m ) ) {
					continue;
				}
				$url = untrailingslashit( html_entity_decode( $m[1] ) );
				if ( ! isset( $map[ $url ] ) ) {
					continue;
				}
				$label      = esc_html( fs_cup_round_label( $map[ $url ][0], $map[ $url ][1], false ) );
				$rows[ $i ] = preg_replace( '#(<div class="cm">)([^<]*?)(?: · [^<]*)?(</div>)#u', '$1$2 · ' . $label . '$3', $seg, 1 );
			}
			$html = implode( '', $rows );
		}

		// 3. List rows (Fixtures/Results/homepage = fs-match-row, old club
		//    pages = fs-c-match-row). Split at each row start; a segment is
		//    one row up to the next, so its first match link is its own.
		$parts = preg_split( '/(?=<div class="fs-(?:c-)?match-row")/', $html );
		if ( count( $parts ) < 2 ) {
			return $html;
		}
		foreach ( $parts as $i => $seg ) {
			if ( $i === 0 || strpos( $seg, 'MW ' ) === false ) {
				continue;
			}
			if ( ! preg_match( '#href="(https?://[^"]*/matches/[^"]+?)/?"#', $seg, $m ) ) {
				continue;
			}
			$url = untrailingslashit( $m[1] );
			if ( ! isset( $map[ $url ] ) ) {
				continue; // not a cup match - leave the row alone
			}
			$parts[ $i ] = preg_replace( '/MW\s+\d+/', esc_html( fs_cup_round_label( $map[ $url ][0], $map[ $url ][1], true ) ), $seg, 1 );
		}
		return implode( '', $parts );
	}

	add_action( 'template_redirect', function () {
		if ( is_admin() || wp_doing_ajax() || is_feed() || ( defined( 'REST_REQUEST' ) && REST_REQUEST ) ) {
			return;
		}
		ob_start( 'fs_cup_filter_html' );
	}, 1 );
}
