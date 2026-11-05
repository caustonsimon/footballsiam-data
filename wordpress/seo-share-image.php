<?php
/**
 * FS share image fallback v1  (added 2026-11-05, companion to "FS SEO extras v1", Snippet 229)
 *
 * Rank Math outputs no og:image on club and player pages and never fires its
 * add_images hook there, so 229's hook does nothing on those pages. This adds
 * og:image, og:image:alt and twitter:image to the finished <head> of a club or
 * player page, but only when the page has no og:image already. The image is
 * the club crest, or the player's photo with the club crest as fallback
 * (fs_seo_page_image() from 229; does nothing if 229 is off).
 *
 * Code Snippets: scope "Only run on site front-end".
 */

add_action( 'template_redirect', function () {
	if ( ! is_singular( array( 'club', 'player' ) ) || ! function_exists( 'fs_seo_page_image' ) ) {
		return;
	}
	ob_start( function ( $html ) {
		if ( ! is_string( $html ) || stripos( $html, 'property="og:image"' ) !== false || stripos( $html, '</head>' ) === false ) {
			return $html;
		}
		$url = fs_seo_page_image();
		if ( ! $url ) {
			return $html;
		}
		$url  = set_url_scheme( $url, 'https' );
		$id   = get_queried_object_id();
		$name = html_entity_decode( get_the_title( $id ), ENT_QUOTES, 'UTF-8' );
		$alt  = is_singular( 'club' ) ? $name . ' crest' : $name;
		$tags = '<meta property="og:image" content="' . esc_url( $url ) . '" />' . "\n"
			. '<meta property="og:image:secure_url" content="' . esc_url( $url ) . '" />' . "\n"
			. '<meta property="og:image:alt" content="' . esc_attr( $alt ) . '" />' . "\n"
			. '<meta name="twitter:image" content="' . esc_url( $url ) . '" />' . "\n";
		return preg_replace( '#</head>#i', $tags . '</head>', $html, 1 );
	} );
}, 3 );
