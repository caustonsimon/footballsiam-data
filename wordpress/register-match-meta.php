<?php
/**
 * register-match-meta.php
 *
 * Exposes the footballsiam `matches` custom fields to the WordPress REST API so
 * the FootyStats daily sync (https://github.com/caustonsimon/footballsiam-data)
 * can read and write them via /wp-json/wp/v2/match.
 *
 * Install one of:
 *   - Code Snippets plugin: new snippet, paste WITHOUT the opening <?php line,
 *     scope "Run everywhere", Save & Activate.
 *   - Child theme functions.php (keep the <?php only if the file needs it).
 *   - mu-plugin: drop this whole file in wp-content/mu-plugins/.
 *
 * All keys are registered as `string`: that matches the values already stored by
 * the initial import and how the theme's fs_get_matches() reads them, and it
 * avoids REST schema-coercion errors on the empty home_score / away_score values.
 */

add_action( 'init', function () {
	$keys = array(
		'footystats_match_id', // idempotency key, int-as-string
		'home_team',           // WP `club` post ID, int-as-string
		'away_team',           // WP `club` post ID, int-as-string
		'home_score',          // '' until played
		'away_score',          // '' until played
		'date',                // YYYYMMDD (required by fs_get_matches() regex)
		'kick_off_time',       // H:i, 24h, Asia/Bangkok
		'match_week',
		'season',              // '2026/2027'
	);

	foreach ( $keys as $key ) {
		register_post_meta( 'matches', $key, array(
			'type'          => 'string',
			'single'        => true,
			'show_in_rest'  => true,
			'auth_callback' => function () {
				// gate writes to logged-in editors; reads follow post visibility
				return current_user_can( 'edit_posts' );
			},
		) );
	}
} );
