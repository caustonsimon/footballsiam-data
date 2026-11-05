<?php
/**
 * FS SEO extras v1  (added 2026-11-05)
 *
 * 1. Share images: club pages share their crest, player pages share the
 *    player's photo (featured image, then the T3 registry photo, then the
 *    current club's crest). Only added when Rank Math has no image of its own.
 * 2. Structured data: a SportsTeam block on club pages and a Person block on
 *    player pages (Rank Math outputs none on these two types).
 * 3. Alt text on the page: crests get "<Club> crest", T3 registry player
 *    photos get the player's name, media-library images take their library
 *    alt text, and any <img> with no alt attribute at all gets alt="".
 *    Images that already have alt text are never touched.
 * 4. Match page titles: "Live Score, Lineups & Stats" stays only for the
 *    competitions with live scores (Thai League 1, ACL Elite, ACL Two,
 *    ASEAN Club Championship). Others read "Result, Report & Stats" once
 *    played and "Fixture & Kick-off Time" before.
 * 5. Matches page: the "MW 1-34" filter is hidden when a cup is chosen.
 *
 * Code Snippets: scope "Only run on site front-end".
 */

if ( ! function_exists( 'fs_seo_club_crest' ) ) {

	function fs_seo_live_comps() {
		return array( 'thai-league-1', 'afc-champions-league-elite', 'afc-champions-league-two', 'asean-club-championship' );
	}

	function fs_seo_club_crest( $club_id ) {
		$club_id = (int) $club_id;
		if ( ! $club_id ) {
			return '';
		}
		$url = (string) get_post_meta( $club_id, 'club_logo_url', true );
		if ( ! $url && has_post_thumbnail( $club_id ) ) {
			$url = (string) get_the_post_thumbnail_url( $club_id, 'full' );
		}
		return $url;
	}

	function fs_seo_player_club( $pid ) {
		$cc = get_post_meta( $pid, 'current_club', true );
		if ( is_array( $cc ) ) {
			$cc = reset( $cc );
		}
		$cc = (int) $cc;
		if ( ! $cc ) {
			$cc = (int) get_post_meta( $pid, 'last_confirmed_club', true );
		}
		return ( $cc && get_post_type( $cc ) === 'club' ) ? $cc : 0;
	}

	function fs_seo_player_photo( $pid ) {
		$url = has_post_thumbnail( $pid ) ? (string) get_the_post_thumbnail_url( $pid, 'full' ) : '';
		if ( ! $url ) {
			$url = (string) get_post_meta( $pid, 'player_photo_url', true );
		}
		if ( ! $url ) {
			$url = fs_seo_club_crest( fs_seo_player_club( $pid ) );
		}
		return $url;
	}

	function fs_seo_page_image() {
		if ( is_singular( 'club' ) ) {
			return fs_seo_club_crest( get_queried_object_id() );
		}
		if ( is_singular( 'player' ) ) {
			return fs_seo_player_photo( get_queried_object_id() );
		}
		return '';
	}

	/* ---- 1. Share images ------------------------------------------- */
	foreach ( array( 'facebook', 'twitter' ) as $fs_net ) {
		add_action( "rank_math/opengraph/{$fs_net}/add_images", function ( $image ) {
			if ( ! is_object( $image ) || ! method_exists( $image, 'add_image_by_url' ) ) {
				return;
			}
			if ( method_exists( $image, 'has_images' ) && $image->has_images() ) {
				return;
			}
			$url = fs_seo_page_image();
			if ( $url ) {
				$image->add_image_by_url( $url );
			}
		} );
	}

	/* ---- 2. Structured data ----------------------------------------- */
	add_action( 'wp_head', function () {
		if ( ! is_singular( array( 'club', 'player' ) ) ) {
			return;
		}
		$id   = get_queried_object_id();
		$name = html_entity_decode( get_the_title( $id ), ENT_QUOTES, 'UTF-8' );
		$url  = get_permalink( $id );
		$m    = function ( $k ) use ( $id ) {
			return trim( (string) get_post_meta( $id, $k, true ) );
		};

		if ( is_singular( 'club' ) ) {
			$d = array( '@context' => 'https://schema.org', '@type' => 'SportsTeam', 'name' => $name, 'url' => $url, 'sport' => 'Soccer' );
			$logo = fs_seo_club_crest( $id );
			if ( $logo ) {
				$d['logo']  = $logo;
				$d['image'] = $logo;
			}
			if ( $m( 'club_nickname' ) ) {
				$d['alternateName'] = $m( 'club_nickname' );
			}
			if ( preg_match( '/^\d{4}$/', $m( 'founded_year' ) ) ) {
				$d['foundingDate'] = $m( 'founded_year' );
			}
			if ( $m( 'stadium_name' ) ) {
				$place = array( '@type' => 'StadiumOrArena', 'name' => $m( 'stadium_name' ) );
				if ( $m( 'city' ) ) {
					$place['address'] = array( '@type' => 'PostalAddress', 'addressLocality' => $m( 'city' ) );
				}
				$d['location'] = $place;
			}
			if ( $m( 'head_coach' ) ) {
				$d['coach'] = array( '@type' => 'Person', 'name' => $m( 'head_coach' ) );
			}
		} else {
			$d = array( '@context' => 'https://schema.org', '@type' => 'Person', 'name' => $name, 'url' => $url, 'jobTitle' => 'Footballer' );
			$img = has_post_thumbnail( $id ) ? get_the_post_thumbnail_url( $id, 'full' ) : $m( 'player_photo_url' );
			if ( $img ) {
				$d['image'] = $img;
			}
			if ( $m( 'nationality' ) ) {
				$d['nationality'] = array( '@type' => 'Country', 'name' => $m( 'nationality' ) );
			}
			$hub = json_decode( (string) get_post_meta( $id, 'fs_player_hub_json', true ), true );
			$dob = is_array( $hub ) && ! empty( $hub['dob'] ) ? (string) $hub['dob'] : '';
			if ( ! $dob && ctype_digit( $m( 'birthday' ) ) && (int) $m( 'birthday' ) > 0 ) {
				$dob = gmdate( 'Y-m-d', (int) $m( 'birthday' ) );
			}
			if ( preg_match( '/^\d{4}-\d{2}-\d{2}$/', $dob ) ) {
				$d['birthDate'] = $dob;
			}
			$club = fs_seo_player_club( $id );
			if ( $club ) {
				$d['affiliation'] = array(
					'@type' => 'SportsTeam',
					'name'  => html_entity_decode( get_the_title( $club ), ENT_QUOTES, 'UTF-8' ),
					'url'   => get_permalink( $club ),
				);
			}
		}
		echo "\n" . '<script type="application/ld+json">' . wp_json_encode( $d, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE ) . '</script>' . "\n";
	}, 20 );

	/* ---- 3. Alt text on the page ------------------------------------ */
	/** image URL => alt text, for every club crest and T3 registry photo. */
	function fs_seo_alt_map() {
		static $map = null;
		if ( $map !== null ) {
			return $map;
		}
		$map = get_transient( 'fs_seo_alt_map_v1' );
		if ( is_array( $map ) ) {
			return $map;
		}
		global $wpdb;
		$map  = array();
		$rows = $wpdb->get_results(
			"SELECT p.post_title t, pm.meta_value u, p.post_type pt FROM {$wpdb->postmeta} pm
			 JOIN {$wpdb->posts} p ON p.ID = pm.post_id
			 WHERE p.post_status = 'publish'
			   AND ( ( p.post_type = 'club' AND pm.meta_key = 'club_logo_url' )
			      OR ( p.post_type = 'player' AND pm.meta_key = 'player_photo_url' ) )
			   AND pm.meta_value <> ''"
		);
		foreach ( (array) $rows as $r ) {
			$t = html_entity_decode( $r->t, ENT_QUOTES, 'UTF-8' );
			$map[ fs_seo_norm_url( $r->u ) ] = $r->pt === 'club' ? $t . ' crest' : $t;
		}
		set_transient( 'fs_seo_alt_map_v1', $map, 12 * HOUR_IN_SECONDS );
		return $map;
	}

	function fs_seo_norm_url( $u ) {
		$u = html_entity_decode( trim( (string) $u ), ENT_QUOTES, 'UTF-8' );
		return preg_replace( '#^https?://#i', '//', strtok( $u, '?' ) );
	}

	add_action( 'save_post_club', function () { delete_transient( 'fs_seo_alt_map_v1' ); } );
	add_action( 'save_post_player', function () { delete_transient( 'fs_seo_alt_map_v1' ); } );
	add_action( 'updated_post_meta', function ( $mid, $pid, $key ) {
		if ( $key === 'club_logo_url' || $key === 'player_photo_url' ) {
			delete_transient( 'fs_seo_alt_map_v1' );
		}
	}, 10, 3 );

	function fs_seo_fix_img( $m ) {
		$tag = $m[0];
		if ( preg_match( '/\salt\s*=\s*("|\')\s*[^"\'\s]/i', $tag ) ) {
			return $tag; // already has real alt text
		}
		$alt = '';
		if ( preg_match( '/\ssrc\s*=\s*("|\')([^"\']+)\1/i', $tag, $s ) ) {
			$map = fs_seo_alt_map();
			$key = fs_seo_norm_url( $s[2] );
			if ( isset( $map[ $key ] ) ) {
				$alt = $map[ $key ];
			}
		}
		if ( $alt === '' && preg_match( '/wp-image-(\d+)/', $tag, $w ) ) {
			$alt = trim( (string) get_post_meta( (int) $w[1], '_wp_attachment_image_alt', true ) );
		}
		$has_attr = (bool) preg_match( '/\salt\s*=/i', $tag );
		if ( $alt === '' && $has_attr ) {
			return $tag; // deliberate alt="" (decorative) with nothing better to offer
		}
		$attr = ' alt="' . esc_attr( $alt ) . '"';
		if ( $has_attr ) {
			return preg_replace( '/\salt\s*=\s*("|\')\s*\1/i', $attr, $tag, 1 );
		}
		return preg_replace( '/^<img\b/i', '<img' . $attr, $tag, 1 );
	}

	/* ---- 5. Matches page: hide the MW filter for cups ---------------- */
	function fs_seo_hide_cup_mw( $html ) {
		$comp = isset( $_GET['fs_comp'] ) ? sanitize_key( wp_unslash( $_GET['fs_comp'] ) ) : '';
		if ( ! in_array( $comp, array( 'thai-fa-cup', 'thai-league-cup' ), true ) ) {
			return $html;
		}
		return preg_replace( '/<select\s+name="fs_mw"/i', '<select name="fs_mw" style="display:none" disabled', $html );
	}

	add_action( 'template_redirect', function () {
		if ( is_admin() || wp_doing_ajax() || is_feed() || ( defined( 'REST_REQUEST' ) && REST_REQUEST ) ) {
			return;
		}
		ob_start( function ( $html ) {
			if ( ! is_string( $html ) || stripos( $html, '<img' ) === false && stripos( $html, 'fs_mw' ) === false ) {
				return $html;
			}
			$html = preg_replace_callback( '/<img\b[^>]*>/i', 'fs_seo_fix_img', $html );
			return fs_seo_hide_cup_mw( $html );
		} );
	}, 2 );

	/* ---- 4. Match page titles --------------------------------------- */
	function fs_seo_match_title( $title ) {
		if ( ! is_singular( 'matches' ) || ! is_string( $title ) || strpos( $title, 'Live Score' ) === false ) {
			return $title;
		}
		$id    = get_queried_object_id();
		$slugs = wp_get_post_terms( $id, 'competition', array( 'fields' => 'slugs' ) );
		if ( ! is_wp_error( $slugs ) && array_intersect( (array) $slugs, fs_seo_live_comps() ) ) {
			return $title;
		}
		$played = trim( (string) get_post_meta( $id, 'home_score', true ) ) !== '';
		return str_replace( 'Live Score, Lineups & Stats', $played ? 'Result, Report & Stats' : 'Fixture & Kick-off Time', $title );
	}
	add_filter( 'rank_math/frontend/title', 'fs_seo_match_title', 20 );
	add_filter( 'rank_math/opengraph/facebook/og_title', 'fs_seo_match_title', 20 );
	add_filter( 'rank_math/opengraph/twitter/twitter_title', 'fs_seo_match_title', 20 );
}
