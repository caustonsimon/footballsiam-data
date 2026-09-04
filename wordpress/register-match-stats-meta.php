<?php
/**
 * register-match-stats-meta.php
 *
 * Exposes the match-stats enrichment fields on the `matches` CPT to the WP REST
 * API so the FootyStats /match enrichment job can write them via
 * /wp-json/wp/v2/match/<id>.  Companion to register-match-meta.php.
 *
 * Install: Code Snippets plugin (paste without the opening <?php line, scope
 * "Run everywhere", Save & Activate), or a child-theme functions.php, or drop
 * this file into wp-content/mu-plugins/.
 *
 * All keys registered as `string` to match how the connector stores them and
 * how the ACF field defs read them (ACF get_field() falls back to raw meta for
 * simple text/number fields). raw_match_data is a base64-encoded JSON string.
 */

add_action( 'init', function () {
	$keys = array(
		'home_corners',      'away_corners',
		'home_offsides',     'away_offsides',
		'home_on_target',    'away_on_target',
		'home_off_target',   'away_off_target',
		'home_attempts',     'away_attempts',
		'home_fouls',        'away_fouls',
		'home_possession',   'away_possession',
		'home_xg_prematch',  'away_xg_prematch',  // added for the daily sync's Part A
		'stadium',
		'raw_match_data',
	);

	foreach ( $keys as $key ) {
		register_post_meta( 'matches', $key, array(
			'type'          => 'string',
			'single'        => true,
			'show_in_rest'  => true,
			'auth_callback' => function () {
				return current_user_can( 'edit_posts' );
			},
		) );
	}
} );
