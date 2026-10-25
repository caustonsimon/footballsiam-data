<?php
/**
 * score-source-meta.php: Code Snippet 205 "FS Score source meta + clash alert v1"
 * (26 Oct 2026).
 *
 * Simon, 26 Oct 2026: if FootyStats still has no score by 23:00 Bangkok, the
 * sync takes it from FotMob instead (src/fotmob_fallback.py). If FootyStats
 * later disagrees, keep the FotMob score and email Simon.
 *
 * Registers three `matches` meta keys for REST so the sync can write them:
 *   score_source     'fotmob' when the score came from the FotMob fallback
 *                    ('' = FootyStats, as before)
 *   fotmob_match_id  the FotMob match the score was taken from
 *   score_clash      '' normally; set by the sync to a one-line description
 *                    when FootyStats later reports a different full-time score
 *
 * Whenever score_clash is set to a new non-empty value, WordPress emails the
 * alert address (FS_MDW_ALERT_EMAIL, else admin_email), the same recipient as
 * the Matchday Watcher's 02:00 held-card email. Clear the meta (or fix the
 * score and clear it) once Simon has decided.
 *
 * Install: Code Snippets, paste without the opening <?php, "Run everywhere".
 */

add_action( 'init', function () {
	foreach ( array( 'score_source', 'fotmob_match_id', 'score_clash' ) as $key ) {
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

if ( ! function_exists( 'fs_score_clash_alert' ) ) {
	function fs_score_clash_alert( $meta_id, $post_id, $meta_key, $value ) {
		if ( 'score_clash' !== $meta_key || '' === trim( (string) $value ) ) {
			return;
		}
		if ( 'matches' !== get_post_type( $post_id ) ) {
			return;
		}
		$to    = defined( 'FS_MDW_ALERT_EMAIL' ) ? FS_MDW_ALERT_EMAIL : get_option( 'admin_email' );
		$title = get_the_title( $post_id );
		$hs    = get_post_meta( $post_id, 'home_score', true );
		$as    = get_post_meta( $post_id, 'away_score', true );
		$edit  = admin_url( 'post.php?post=' . (int) $post_id . '&action=edit' );
		wp_mail(
			$to,
			"Football Siam: score clash on {$title}",
			"The site shows {$title} {$hs}-{$as}, taken from FotMob because FootyStats had no score by 23:00.\n\n"
			. "FootyStats now reports a different result: {$value}\n\n"
			. "The site score has NOT been changed. Check the game and decide which is right.\n"
			. "If FotMob was wrong, correct home_score / away_score on the match and clear score_clash.\n"
			. "If FotMob was right, just clear score_clash.\n\n"
			. "Match post {$post_id}: {$edit}\n"
		);
	}
	add_action( 'added_post_meta', 'fs_score_clash_alert', 10, 4 );
	add_action( 'updated_post_meta', 'fs_score_clash_alert', 10, 4 );
}
