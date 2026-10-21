<?php
/**
 * FS Thai FA Cup round labels v1  (added 2026-10-21)
 *
 * FootyStats has no match weeks for the FA Cup, so the sync stores the ROUND
 * NUMBER in match_week for every `thai-fa-cup` match:
 *   0 Qualifying Round, 1 First Round, 2 Second Round, 3 Third Round,
 *   4 Quarter-final, 5 Semi-final, 6 Final
 *
 * Every fixtures/results list on the site (Fixtures, Results, homepage, club
 * pages) prints "MW n", and the single match page prints "Match Week n".
 * For FA Cup matches only, this swaps those for the round name. It works on
 * the finished page HTML, so none of the existing render snippets
 * (146 / 156-158 / 179) are touched: each list row carries the match's own
 * permalink, and only rows whose permalink belongs to an FA Cup post change.
 * League rows are left exactly as they were. Deactivate to restore "MW n".
 *
 * Code Snippets: scope "Only run on site front-end".
 */

if ( ! function_exists( 'fs_facup_round_label' ) ) {

	function fs_facup_round_label( $n, $short = true ) {
		$short_l = array( 'Qual. Rd', 'Round 1', 'Round 2', 'Round 3', 'QF', 'SF', 'Final' );
		$full_l  = array( 'Qualifying Round', 'First Round', 'Second Round', 'Third Round', 'Quarter-final', 'Semi-final', 'Final' );
		$n = (int) $n;
		$l = $short ? $short_l : $full_l;
		return isset( $l[ $n ] ) ? $l[ $n ] : ( $short ? 'Round ' . $n : 'Round ' . $n );
	}

	/** permalink => round number, for every FA Cup match. Cached for an hour. */
	function fs_facup_round_map() {
		static $map = null;
		if ( $map !== null ) {
			return $map;
		}
		$map = get_transient( 'fs_facup_round_map_v1' );
		if ( is_array( $map ) ) {
			return $map;
		}
		$map = array();
		$ids = get_posts( array(
			'post_type'      => 'matches',
			'post_status'    => 'publish',
			'posts_per_page' => -1,
			'fields'         => 'ids',
			'no_found_rows'  => true,
			'tax_query'      => array( array(
				'taxonomy' => 'competition',
				'field'    => 'slug',
				'terms'    => array( 'thai-fa-cup' ),
			) ),
		) );
		foreach ( $ids as $id ) {
			$map[ untrailingslashit( get_permalink( $id ) ) ] = (int) get_post_meta( $id, 'match_week', true );
		}
		set_transient( 'fs_facup_round_map_v1', $map, HOUR_IN_SECONDS );
		return $map;
	}

	/** New or updated FA Cup match: rebuild the map on the next page view. */
	add_action( 'save_post_matches', function () {
		delete_transient( 'fs_facup_round_map_v1' );
	} );
	add_action( 'updated_post_meta', function ( $mid, $post_id, $key ) {
		if ( $key === 'match_week' && get_post_type( $post_id ) === 'matches' ) {
			delete_transient( 'fs_facup_round_map_v1' );
		}
	}, 10, 3 );

	function fs_facup_filter_html( $html ) {
		if ( ! is_string( $html ) || strpos( $html, '/matches/' ) === false ) {
			return $html;
		}
		$map = fs_facup_round_map();
		if ( empty( $map ) ) {
			return $html;
		}

		// 1. Single FA Cup match page: "Match Week n" -> "First Round" etc.
		if ( is_singular( 'matches' ) ) {
			$self = untrailingslashit( get_permalink( get_queried_object_id() ) );
			if ( isset( $map[ $self ] ) ) {
				$label = fs_facup_round_label( $map[ $self ], false );
				$html  = preg_replace( '/Match Week\s+\d+/', esc_html( $label ), $html, 1 );
			}
		}

		// 2. List rows (Fixtures/Results/homepage = fs-match-row, club pages =
		//    fs-c-match-row). Split at each row start; a segment is one row up
		//    to the next row, so the first match link in it is that row's own.
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
				continue; // not an FA Cup match - leave the row alone
			}
			$parts[ $i ] = preg_replace( '/MW\s+\d+/', esc_html( fs_facup_round_label( $map[ $url ], true ) ), $seg, 1 );
		}
		return implode( '', $parts );
	}

	add_action( 'template_redirect', function () {
		if ( is_admin() || wp_doing_ajax() || is_feed() || ( defined( 'REST_REQUEST' ) && REST_REQUEST ) ) {
			return;
		}
		ob_start( 'fs_facup_filter_html' );
	}, 1 );
}
