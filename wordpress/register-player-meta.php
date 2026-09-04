<?php
/**
 * register-player-meta.php
 *
 * Exposes the plain (non-ACF) `player` meta keys that the daily sync's Part A
 * writes when it auto-creates a player for a newly completed match:
 *
 *   last_confirmed_club  - WP `club` post id of the side the player lined up
 *                          for in the match that triggered their creation.
 *                          Historical fact only - NOT a current-squad claim,
 *                          which is why the sync never sets `current_club` here.
 *   last_confirmed_date  - that match's date, YYYYMMDD.
 *   birthday             - FootyStats `birthday` (unix seconds), when known.
 *
 * The ACF fields on `player` (footystats_id, first_name, last_name, nationality,
 * position, current_club) are written through the REST `acf` object and do NOT
 * need this snippet. Without this snippet, a POST /wp-json/wp/v2/player with a
 * `meta` object silently drops these three keys.
 *
 * Install: Code Snippets plugin (paste without the opening <?php line, scope
 * "Run everywhere", Save & Activate), or a child-theme functions.php, or drop
 * into wp-content/mu-plugins/.
 */

add_action( 'init', function () {
	$keys = array( 'last_confirmed_club', 'last_confirmed_date', 'birthday' );

	foreach ( $keys as $key ) {
		register_post_meta( 'player', $key, array(
			'type'          => 'string',
			'single'        => true,
			'show_in_rest'  => true,
			'auth_callback' => function () {
				return current_user_can( 'edit_posts' );
			},
		) );
	}
} );
