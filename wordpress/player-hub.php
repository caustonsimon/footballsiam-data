<?php
/**
 * FS Player Hub v1  (added 2026-10-22, Port first)
 *
 * Full player profile page for players whose data comes from the FotMob /
 * Thai League squad sync (player meta fs_player_hub_json, written by
 * footballsiam-data src/fotmob_squads.py). Sections: hero with the official
 * Thai League photo, English and Thai names and this season's numbers;
 * season by competition; position map; FotMob/Opta stats with league
 * percentiles (totals or per 90); shot map; ratings by match; match log;
 * career, international record and trophies.
 *
 * Any player without the feed keeps the old page. ?fs_oldplayer=1 shows the
 * old page for any player.
 */

if ( ! function_exists( 'fsp_render' ) ) {

function fsp_e( $s ) { return htmlspecialchars( (string) $s, ENT_QUOTES, 'UTF-8' ); }

function fsp_age( $dob ) {
	if ( ! $dob ) return '';
	try {
		$d = new DateTime( $dob, new DateTimeZone( 'Asia/Bangkok' ) );
		return (string) $d->diff( new DateTime( 'now', new DateTimeZone( 'Asia/Bangkok' ) ) )->y;
	} catch ( Exception $e ) { return ''; }
}

function fsp_fdate( $ymd, $fmt = 'j M Y' ) {
	try { return ( new DateTime( $ymd ) )->format( $fmt ); } catch ( Exception $e ) { return $ymd; }
}

function fsp_comp_short( $c ) {
	$m = array( 'AFC Champions League Elite' => 'ACL Elite', 'AFC Champions League Two' => 'ACL Two',
		'ASEAN Club Championship' => 'ASEAN Club Champ.', 'Thai FA Cup' => 'FA Cup', 'Thai League Cup' => 'League Cup' );
	return isset( $m[ $c ] ) ? $m[ $c ] : $c;
}

function fsp_data( $pid ) {
	$raw = get_post_meta( $pid, 'fs_player_hub_json', true );
	$h = $raw ? json_decode( $raw, true ) : null;
	if ( ! is_array( $h ) || empty( $h['name'] ) ) return null;
	$photo = get_the_post_thumbnail_url( $pid, 'full' );
	$club = (int) ( $h['wp_club'] ?? 0 );
	return array(
		'h' => $h,
		'photo' => $photo ?: '',
		'club_url' => $club ? get_permalink( $club ) : '',
		'club_logo' => ( $club && function_exists( 'fs_get_club_logo' ) ) ? fs_get_club_logo( $club ) : '',
	);
}

function fsp_render( $d ) {
	$e = 'fsp_e';
	$h = $d['h'];
	$s = $h['season'] ?: array( 'apps' => 0, 'starts' => 0, 'mins' => 0, 'goals' => 0, 'assists' => 0, 'yellow' => 0, 'red' => 0, 'rating' => null );
	$bc = $h['by_comp'] ?: array();
	$gk = ( $h['pos'] ?? '' ) === 'GK';
	$age = fsp_age( $h['dob'] ?? '' );
	$order = array( 'Thai League 1', 'Thai League 2', 'AFC Champions League Elite', 'AFC Champions League Two', 'ASEAN Club Championship', 'Thai FA Cup', 'Thai League Cup' );
	uksort( $bc, function ( $a, $b ) use ( $order ) { $x = array_search( $a, $order ); $y = array_search( $b, $order ); return ( $x === false ? 99 : $x ) - ( $y === false ? 99 : $y ); } );
	$matches = $h['matches'] ?? array();
	$rated = array_reverse( array_values( array_filter( $matches, function ( $m ) { return ! empty( $m['rating'] ); } ) ) );
	$rated = array_slice( $rated, -12 );
	$deep = $h['deep'] ?? array();
	$shots = $h['shots'] ?? array();
	$hero_tiles = $gk
		? array( array( 'Apps', $s['apps'] ), array( 'Starts', $s['starts'] ), array( 'Minutes', $s['mins'] ), array( 'Rating', $s['rating'] ? number_format( $s['rating'], 2 ) : '–' ) )
		: array( array( 'Apps', $s['apps'] ), array( 'Goals', $s['goals'] ), array( 'Assists', $s['assists'] ), array( 'Minutes', $s['mins'] ), array( 'Rating', $s['rating'] ? number_format( $s['rating'], 2 ) : '–' ) );
	$facts = array_filter( array(
		'Nationality' => $h['nat'] ?? '',
		'Age' => $age ? $age . ( ! empty( $h['dob'] ) ? ' (' . fsp_fdate( $h['dob'] ) . ')' : '' ) : '',
		'Height' => ! empty( $h['height'] ) ? $h['height'] . ' cm' : '',
		'Foot' => $h['foot'] ?? '',
		'Contract' => ! empty( $h['contract_end'] ) ? 'Until ' . fsp_fdate( $h['contract_end'], 'M Y' ) : '',
	) );
	ob_start();
	?>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Lato:wght@400;700;900&family=Playfair+Display:wght@700;900&family=Noto+Serif+Thai:wght@600;700&family=Noto+Sans+Thai:wght@400;600&display=swap">
<div class="fsp">
<style>
.fsp{--g:#0A3B25;--gd:#e3c567;--ink:#111;--tx:#333;--mu:#767676;--hl:#e6e6e6;font-family:'Lato','Noto Sans Thai',sans-serif;color:var(--tx);background:#fdfdfd}
.fsp *{box-sizing:border-box}.fsp a{color:inherit}
.fsp-wrap{max-width:1180px;margin:0 auto;padding:0 20px}
.fsp-hero{background:var(--g);color:#fff;position:relative;overflow:hidden}
.fsp-hero:after{content:"";position:absolute;left:0;right:0;bottom:0;height:6px;background:var(--gd)}
.fsp-ele{position:absolute;left:-60px;bottom:-30px;height:360px;opacity:.08;pointer-events:none}
.fsp-hero-in{position:relative;z-index:1;max-width:1180px;margin:0 auto;padding:30px 20px 0;display:grid;grid-template-columns:330px 1fr;gap:34px;align-items:end}
.fsp-photo{position:relative;height:340px;display:flex;align-items:flex-end;justify-content:center}
.fsp-photo img{max-height:340px;max-width:100%;object-fit:contain;display:block;filter:drop-shadow(0 10px 18px rgba(0,0,0,.35))}
.fsp-photo .num{position:absolute;left:0;top:14px;font-family:'Playfair Display',serif;font-weight:900;font-size:96px;line-height:1;color:var(--gd);opacity:.95;z-index:0}
.fsp-photo img{position:relative;z-index:1}
.fsp-photo.none{align-items:center}.fsp-photo.none .num{position:static;font-size:160px}
.fsp-id{padding-bottom:30px}
.fsp-kick{color:var(--gd);font-weight:900;letter-spacing:2px;text-transform:uppercase;font-size:12px;display:flex;align-items:center;gap:10px}
.fsp-kick img{width:26px;height:26px;object-fit:contain;background:#fff;border-radius:50%;padding:2px}
.fsp-kick a{text-decoration:none;color:var(--gd)}
.fsp-h1{font-family:'Playfair Display',serif;font-weight:900;text-transform:uppercase;font-size:52px;line-height:1.02;margin:10px 0 6px;color:#fff!important}
.fsp-th{font-family:'Noto Serif Thai',serif;font-weight:700;font-size:26px;color:var(--gd);margin:0 0 16px;line-height:1.4}
.fsp-facts{display:flex;flex-wrap:wrap;gap:8px 26px;font-size:13px;margin-bottom:22px}
.fsp-facts b{display:block;color:var(--gd);font-size:10px;letter-spacing:1.5px;text-transform:uppercase;font-weight:900;margin-bottom:2px}
.fsp-tiles{display:flex;gap:0;background:rgba(255,255,255,.07);border-top:1px solid rgba(227,197,103,.35);border-radius:6px 6px 0 0;overflow:hidden}
.fsp-tiles div{flex:1;text-align:center;padding:14px 6px 18px;border-right:1px solid rgba(255,255,255,.1)}
.fsp-tiles div:last-child{border-right:0}
.fsp-tiles b{display:block;font-family:'Playfair Display',serif;font-weight:900;font-size:34px;color:#fff;line-height:1.05}
.fsp-tiles span{font-size:10px;letter-spacing:1.5px;text-transform:uppercase;opacity:.75;font-weight:900}
.fsp-badge{display:inline-block;background:#d93025;color:#fff;font-size:11px;font-weight:900;letter-spacing:1px;text-transform:uppercase;padding:4px 8px;border-radius:3px;margin-bottom:14px}
.fsp-nav{position:sticky;top:0;z-index:20;background:#fff;border-bottom:1px solid var(--hl);box-shadow:0 2px 6px rgba(0,0,0,.04)}
.fsp-nav .fsp-wrap{display:flex;gap:4px;overflow-x:auto;scrollbar-width:none}
.fsp-nav a{padding:15px 14px;font-weight:900;font-size:12px;letter-spacing:1px;text-transform:uppercase;text-decoration:none;color:var(--ink);border-bottom:3px solid transparent;white-space:nowrap}
.fsp-nav a:hover,.fsp-nav a.on{border-bottom-color:var(--gd);color:var(--g)}
.fsp-sec{padding:36px 0 4px;scroll-margin-top:60px}
.fsp-h2{font-family:'Playfair Display',serif;font-weight:900;text-transform:uppercase;font-size:26px;color:var(--ink);margin:0;line-height:1.15}
.fsp-rule{width:70px;height:4px;background:var(--gd);margin:10px 0 18px}
.fsp-sub{color:var(--mu);font-size:13px;margin:-8px 0 16px}
.fsp-card{background:#fff;border-radius:8px;box-shadow:0 2px 5px rgba(0,0,0,.05);border:1px solid #eee}
.fsp-g2{display:grid;grid-template-columns:1.5fr 1fr;gap:18px}
.fsp-g2e{display:grid;grid-template-columns:1fr 1fr;gap:18px}
.fsp-g2>*,.fsp-g2e>*{min-width:0}
.fsp-g3{columns:3;column-gap:16px}
.fsp-g3>.fsp-card{break-inside:avoid;margin-bottom:16px;display:inline-block;width:100%}
.fsp-ch{padding:18px 20px}
.fsp-ch h4{margin:0 0 4px;font-size:13px;font-weight:900;letter-spacing:1px;text-transform:uppercase;color:var(--ink)}
.fsp-ch .k{font-size:11px;color:var(--mu);margin-bottom:12px;display:flex;flex-wrap:wrap;gap:12px}
.fsp-ch .k i{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:5px;vertical-align:-1px}
.fsp-t{width:100%;border-collapse:collapse;font-size:13px}
.fsp-t th{background:var(--g);color:#fff;font-size:11px;letter-spacing:1px;text-transform:uppercase;padding:10px 8px;text-align:center;white-space:nowrap}
.fsp-t th.l,.fsp-t td.l{text-align:left}
.fsp-t td{padding:9px 8px;text-align:center;border-bottom:1px solid #eee}
.fsp-t tr:nth-child(even) td{background:#f9f9f9}
.fsp-t tr.tot td{background:rgba(227,197,103,.3)!important;font-weight:900}
.fsp-t td.z{color:#bbb}
.fsp-tw{overflow-x:auto}
.fsp-pitch{display:flex;gap:18px;align-items:center}
.fsp-pitch svg{flex-shrink:0}
.fsp-pos-list div{font-size:13px;margin-bottom:6px;display:flex;align-items:center;gap:8px}
.fsp-pos-list i{width:12px;height:12px;border-radius:50%;display:inline-block}
.fsp-tog{display:inline-flex;border:1px solid var(--g);border-radius:4px;overflow:hidden;margin-left:auto}
.fsp-tog button{background:#fff;border:0;padding:7px 12px;font-family:'Lato';font-weight:900;font-size:11px;letter-spacing:1px;text-transform:uppercase;color:var(--g);cursor:pointer}
.fsp-tog button.on{background:var(--g);color:#fff}
.fsp-grp{padding:16px 18px}
.fsp-grp h5{margin:0 0 12px;font-size:12px;font-weight:900;letter-spacing:2px;text-transform:uppercase;color:var(--g);padding-bottom:7px;border-bottom:2px solid var(--g)}
.fsp-st{margin-bottom:11px}
.fsp-st .r{display:flex;justify-content:space-between;font-size:13px;margin-bottom:4px}
.fsp-st .r b{color:var(--ink)}
.fsp-st .bar{height:7px;background:#eeeeea;border-radius:4px;overflow:hidden;position:relative}
.fsp-st .bar i{display:block;height:100%;background:var(--g);border-radius:4px}
.fsp-st .bar i.hi{background:var(--gd)}
.fsp-st .pc{font-size:10px;color:var(--mu);margin-top:2px;text-align:right}
.fsp-rb{display:flex;gap:6px;align-items:flex-end;height:170px;border-bottom:2px solid var(--ink);padding-top:18px}
.fsp-rb div{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:flex-end;height:100%;position:relative}
.fsp-rb i{display:block;width:70%;max-width:30px;border-radius:3px 3px 0 0}
.fsp-rb span{font-size:10px;font-weight:900;color:var(--ink);margin-bottom:3px}
.fsp-rb em{position:absolute;bottom:-34px;font-style:normal;font-size:9px;color:var(--mu);text-align:center;line-height:1.2;width:100%}
.fsp-rbw{padding-bottom:36px}
.fsp-res{display:inline-block;min-width:54px;font-weight:900;font-size:12px;padding:4px 7px;border-radius:4px;color:#fff}
.fsp-W{background:#1f8a4c}.fsp-D{background:#c9c9c9;color:#111!important}.fsp-L{background:#d93025}
.fsp-rt{display:inline-block;font-weight:900;font-size:12px;padding:3px 7px;border-radius:4px;background:#eef3ef;color:var(--g);min-width:40px}
.fsp-rt.hi{background:var(--g);color:#fff}.fsp-rt.top{background:var(--gd);color:var(--g)}
.fsp-card-y{display:inline-block;width:9px;height:12px;background:#f2c200;border-radius:1px;vertical-align:-1px}
.fsp-card-r{display:inline-block;width:9px;height:12px;background:#d93025;border-radius:1px;vertical-align:-1px}
.fsp-intl{font-size:10px;font-weight:900;color:var(--g);border:1px solid var(--g);border-radius:3px;padding:1px 4px;margin-left:6px;letter-spacing:.5px}
.fsp-car{padding:6px 18px 10px}
.fsp-car div.row{display:grid;grid-template-columns:96px 1fr 60px 60px;gap:10px;align-items:center;padding:11px 0;border-bottom:1px solid #eee;font-size:14px}
.fsp-car div.row:last-child{border-bottom:0}
.fsp-car .yr{font-size:12px;color:var(--mu);font-weight:700}
.fsp-car .tm{font-weight:900;color:var(--ink)}
.fsp-car .tm small{font-weight:700;color:var(--g);font-size:10px;letter-spacing:1px;text-transform:uppercase;margin-left:6px}
.fsp-car .n{text-align:center;font-weight:900}
.fsp-car .hd{font-size:10px;letter-spacing:1px;text-transform:uppercase;color:var(--mu);font-weight:900;border-bottom:2px solid var(--g)}
.fsp-tro{padding:14px 18px}
.fsp-tro div{display:flex;gap:12px;align-items:baseline;padding:8px 0;border-bottom:1px solid #eee;font-size:13px}
.fsp-tro div:last-child{border-bottom:0}
.fsp-tro b{color:var(--ink);font-size:14px}
.fsp-tro .w{color:var(--mu);margin-left:auto;text-align:right}
.fsp-tro .cup{font-size:15px;flex-shrink:0}
.fsp-next{padding:18px 20px;border-top:4px solid var(--g);margin-top:18px}
.fsp-next .l{font-size:11px;font-weight:900;letter-spacing:1.5px;text-transform:uppercase;color:var(--g)}
.fsp-next .m{font-family:'Playfair Display',serif;font-weight:900;font-size:20px;text-transform:uppercase;color:var(--ink);margin:6px 0 2px}
.fsp-next .c{font-size:12px;color:var(--mu)}
.fsp-src{font-size:11px;color:var(--mu);padding:18px 0 40px;border-top:1px solid #eee;margin-top:26px}
@media (max-width:900px){
 .fsp-hero-in{grid-template-columns:1fr;gap:0;padding-top:20px}
 .fsp-photo{height:250px}.fsp-photo img{max-height:250px}.fsp-photo .num{font-size:72px;left:4%}
 .fsp-h1{font-size:38px}.fsp-th{font-size:22px}
 .fsp-g2,.fsp-g2e{grid-template-columns:1fr}.fsp-g3{columns:2}
}
@media (max-width:560px){
 .fsp-wrap{padding:0 16px}.fsp-h1{font-size:32px}.fsp-g3{columns:1}.fsp-rb{gap:3px}.fsp-rb em{font-size:8px}
 .fsp-tiles b{font-size:24px}.fsp-tiles span{font-size:9px;letter-spacing:.5px}
 .fsp-car div.row{grid-template-columns:76px 1fr 46px 46px;font-size:13px}
 .fsp-pitch{flex-direction:column;align-items:flex-start}
}
</style>

<section class="fsp-hero">
 <img class="fsp-ele" src="https://footballsiam.com/wp-content/uploads/2026/10/fs-erawan-gold.png" alt="">
 <div class="fsp-hero-in">
  <div class="fsp-photo<?php echo $d['photo'] ? '' : ' none'; ?>">
   <?php if ( $h['number'] !== null && $h['number'] !== '' ) : ?><div class="num"><?php echo $e( $h['number'] ); ?></div><?php endif; ?>
   <?php if ( $d['photo'] ) : ?><img src="<?php echo $e( $d['photo'] ); ?>" alt="<?php echo $e( $h['name'] ); ?>"><?php endif; ?>
  </div>
  <div class="fsp-id">
   <div class="fsp-kick"><?php if ( $d['club_logo'] ) echo '<img src="' . $e( $d['club_logo'] ) . '" alt="">'; ?>
    <a href="<?php echo $e( $d['club_url'] ); ?>"><?php echo $e( $h['club'] ); ?></a><span>·</span><span><?php echo $e( str_replace( 'Keeper', 'Goalkeeper', $h['primary_position'] ?: $h['pos_label'] ) ); ?></span><?php if ( ! empty( $h['captain'] ) ) echo '<span>· Captain</span>'; ?></div>
   <h1 class="fsp-h1"><?php echo $e( $h['name'] ); ?></h1>
   <?php if ( ! empty( $h['name_th'] ) ) : ?><div class="fsp-th" lang="th"><?php echo $e( $h['name_th'] ); ?></div><?php endif; ?>
   <?php if ( ! empty( $h['injury'] ) && ! empty( $h['injury']['name'] ) ) : ?><div class="fsp-badge">Injured: <?php echo $e( $h['injury']['name'] ); ?></div><?php endif; ?>
   <div class="fsp-facts"><?php foreach ( $facts as $k => $v ) echo '<div><b>' . $e( $k ) . '</b>' . $e( $v ) . '</div>'; ?></div>
   <div class="fsp-tiles"><?php foreach ( $hero_tiles as $t ) echo '<div><b>' . $e( $t[1] ) . '</b><span>' . $e( $t[0] ) . '</span></div>'; ?></div>
  </div>
 </div>
</section>

<nav class="fsp-nav"><div class="fsp-wrap">
 <a href="#fsp-season">Season</a><?php if ( ! empty( $deep['groups'] ) ) : ?><a href="#fsp-stats">Stats</a><?php endif; ?>
 <a href="#fsp-log">Match log</a><a href="#fsp-career">Career</a>
</div></nav>

<div class="fsp-wrap">

<section class="fsp-sec" id="fsp-season">
 <h2 class="fsp-h2"><?php echo $e( $h['season_label'] ); ?> season</h2><div class="fsp-rule"></div>
 <div class="fsp-g2">
  <div>
   <div class="fsp-card fsp-tw"><table class="fsp-t"><thead><tr><th class="l">Competition</th><th>Apps</th><th>Starts</th><th>Mins</th><?php if ( ! $gk ) : ?><th>Goals</th><th>Ast</th><?php endif; ?><th>YC</th><th>RC</th><th>Rating</th></tr></thead><tbody>
   <?php foreach ( $bc as $c => $b ) : ?>
    <tr><td class="l"><b><?php echo $e( $c ); ?></b></td><td><?php echo (int) $b['apps']; ?></td><td><?php echo (int) $b['starts']; ?></td><td><?php echo (int) $b['mins']; ?></td>
    <?php if ( ! $gk ) : ?><td class="<?php echo $b['goals'] ? '' : 'z'; ?>"><?php echo (int) $b['goals']; ?></td><td class="<?php echo $b['assists'] ? '' : 'z'; ?>"><?php echo (int) $b['assists']; ?></td><?php endif; ?>
    <td class="<?php echo $b['yellow'] ? '' : 'z'; ?>"><?php echo (int) $b['yellow']; ?></td><td class="<?php echo $b['red'] ? '' : 'z'; ?>"><?php echo (int) $b['red']; ?></td><td><?php echo $b['rating'] ? $e( number_format( $b['rating'], 2 ) ) : '–'; ?></td></tr>
   <?php endforeach; ?>
    <tr class="tot"><td class="l">All competitions</td><td><?php echo (int) $s['apps']; ?></td><td><?php echo (int) $s['starts']; ?></td><td><?php echo (int) $s['mins']; ?></td>
    <?php if ( ! $gk ) : ?><td><?php echo (int) $s['goals']; ?></td><td><?php echo (int) $s['assists']; ?></td><?php endif; ?><td><?php echo (int) $s['yellow']; ?></td><td><?php echo (int) $s['red']; ?></td><td><?php echo $s['rating'] ? $e( number_format( $s['rating'], 2 ) ) : '–'; ?></td></tr>
   </tbody></table></div>
   <?php if ( empty( $bc ) ) echo '<p class="fsp-sub" style="margin-top:12px">No first-team appearances yet this season.</p>'; ?>
   <?php if ( $rated ) : ?>
   <div class="fsp-card fsp-ch fsp-rbw" style="margin-top:18px"><h4>Match ratings</h4>
    <div class="k"><span><i style="background:#e3c567"></i>7.5 and above</span><span><i style="background:#0A3B25"></i>6.5 to 7.4</span><span><i style="background:rgba(10,59,37,.4)"></i>Below 6.5</span></div>
    <div class="fsp-rb"><?php foreach ( $rated as $m ) : $r = (float) $m['rating']; $hgt = max( 6, min( 100, ( $r - 4 ) / 6 * 100 ) );
     $col = $r >= 7.5 ? '#e3c567' : ( $r >= 6.5 ? '#0A3B25' : 'rgba(10,59,37,.4)' ); ?>
     <div title="<?php echo $e( fsp_fdate( $m['date'] ) . ' ' . ( $m['home'] ? 'v ' : 'at ' ) . $m['opp'] ); ?>"><span><?php echo $e( number_format( $r, 1 ) ); ?></span><i style="height:<?php echo round( $hgt ); ?>%;background:<?php echo $col; ?>"></i><em><?php echo $e( fsp_fdate( $m['date'], 'j M' ) ); ?><br><?php echo $e( $m['opp'] ); ?></em></div>
    <?php endforeach; ?></div>
   </div>
   <?php endif; ?>
  </div>
  <div>
   <?php if ( ! empty( $h['positions'] ) ) : ?>
   <div class="fsp-card fsp-ch"><h4>Positions played</h4>
    <div class="fsp-pitch">
     <svg width="150" height="215" viewBox="0 0 100 143" aria-hidden="true">
      <rect x="1" y="1" width="98" height="141" rx="2" fill="#0A3B25"/><g fill="none" stroke="rgba(255,255,255,.45)" stroke-width=".8">
      <rect x="4" y="4" width="92" height="135"/><line x1="4" y1="71.5" x2="96" y2="71.5"/><circle cx="50" cy="71.5" r="11"/>
      <rect x="25" y="4" width="50" height="20"/><rect x="38" y="4" width="24" height="7"/><rect x="25" y="119" width="50" height="20"/><rect x="38" y="132" width="24" height="7"/></g>
      <?php foreach ( $h['positions'] as $p ) : if ( $p['top'] === null ) continue; $cx = 4 + 92 * (float) $p['right']; $cy = 4 + 135 * (float) $p['top']; ?>
       <circle cx="<?php echo round( $cx, 1 ); ?>" cy="<?php echo round( $cy, 1 ); ?>" r="<?php echo $p['main'] ? 8 : 6; ?>" fill="<?php echo $p['main'] ? '#e3c567' : 'rgba(255,255,255,.75)'; ?>"/>
       <text x="<?php echo round( $cx, 1 ); ?>" y="<?php echo round( $cy + 2.3, 1 ); ?>" text-anchor="middle" font-size="5.5" font-weight="900" font-family="Lato,sans-serif" fill="#0A3B25"><?php echo $e( $p['short'] ); ?></text>
      <?php endforeach; ?>
     </svg>
     <div class="fsp-pos-list"><?php foreach ( $h['positions'] as $p ) echo '<div><i style="background:' . ( $p['main'] ? '#e3c567' : '#9fb3a8' ) . '"></i>' . $e( $p['label'] ) . ( $p['main'] ? ' <b>(main)</b>' : '' ) . '</div>'; ?></div>
    </div>
   </div>
   <?php endif; ?>
   <?php if ( ! empty( $h['next'] ) ) : $nt = strtotime( $h['next']['utc'] ); ?>
   <div class="fsp-card fsp-next"><div class="l">Next match</div><div class="m"><?php echo $e( $h['next']['home'] . ' v ' . $h['next']['away'] ); ?></div>
    <div class="c"><?php echo $e( fsp_comp_short( $h['next']['comp'] ) ); ?> · <?php echo $e( ( new DateTime( '@' . $nt ) )->setTimezone( new DateTimeZone( 'Asia/Bangkok' ) )->format( 'D j M, H:i' ) ); ?> Bangkok time</div></div>
   <?php endif; ?>
  </div>
 </div>
</section>

<?php if ( ! empty( $deep['groups'] ) ) : ?>
<section class="fsp-sec" id="fsp-stats">
 <div style="display:flex;align-items:flex-end;gap:14px;flex-wrap:wrap"><div><h2 class="fsp-h2">Stats</h2><div class="fsp-rule" style="margin-bottom:0"></div></div>
  <div class="fsp-tog" role="group"><button type="button" class="on" data-m="t">Totals</button><button type="button" data-m="p">Per 90</button></div></div>
 <p class="fsp-sub" style="margin:14px 0 16px"><?php echo $e( $deep['label'] ); ?>. Bars show the percentile rank against other players in the league; gold is the top 20%.</p>
 <div class="fsp-g3">
 <?php foreach ( $deep['groups'] as $g ) : ?>
  <div class="fsp-card fsp-grp"><h5><?php echo $e( $g['title'] ); ?></h5>
  <?php foreach ( $g['items'] as $it ) :
	$pct = $it['pct'] !== null ? (float) $it['pct'] : null; $pct90 = $it['pct90'] !== null ? (float) $it['pct90'] : $pct;
	$v = $it['v']; if ( $it['fmt'] === 'percent' ) $v .= '%';
	$p90 = $it['p90'] !== null ? number_format( (float) $it['p90'], ( $it['fmt'] === 'percent' ? 1 : 2 ) ) . ( $it['fmt'] === 'percent' ? '%' : '' ) : $v; ?>
   <div class="fsp-st" data-t="<?php echo $e( $v ); ?>" data-p="<?php echo $e( $p90 ); ?>" data-tp="<?php echo $pct === null ? '' : round( $pct ); ?>" data-pp="<?php echo $pct90 === null ? '' : round( $pct90 ); ?>">
    <div class="r"><span><?php echo $e( $it['t'] ); ?></span><b class="v"><?php echo $e( $v ); ?></b></div>
    <?php if ( $pct !== null ) : ?><div class="bar"><i class="<?php echo $pct >= 80 ? 'hi' : ''; ?>" style="width:<?php echo max( 2, round( $pct ) ); ?>%"></i></div><div class="pc"><?php echo round( $pct ); ?>th percentile</div><?php endif; ?>
   </div>
  <?php endforeach; ?></div>
 <?php endforeach; ?>
 </div>
 <?php if ( $shots ) :
	$goals = count( array_filter( $shots, function ( $x ) { return $x['goal']; } ) );
	$xg = array_sum( array_map( function ( $x ) { return (float) $x['xg']; }, $shots ) ); ?>
 <div class="fsp-card fsp-ch" style="margin-top:18px"><h4>Shot map, <?php echo $e( $deep['label'] ); ?></h4>
  <div class="k"><span><?php echo count( $shots ); ?> shots · <?php echo $goals; ?> goals · <?php echo $e( number_format( $xg, 2 ) ); ?> xG</span><span><i style="background:#e3c567"></i>Goal</span><span><i style="background:#0A3B25"></i>On target</span><span><i style="background:#fff;border:2px solid #0A3B25"></i>Off target or blocked</span><span>Bigger dot = better chance</span></div>
  <svg viewBox="0 0 68 36" style="width:100%;max-width:620px;display:block;margin:0 auto" aria-label="Shot map">
   <rect x="0" y="0" width="68" height="36" fill="#f4f6f1"/><g fill="none" stroke="#b9c7bd" stroke-width=".3">
   <path d="M.5 36 V.5 H67.5 V36"/><rect x="13.85" y=".5" width="40.3" height="16.5"/><rect x="24.85" y=".5" width="18.3" height="5.5"/>
   <path d="M26.7 17 A9.15 9.15 0 0 0 41.3 17"/><line x1="30.34" y1=".5" x2="37.66" y2=".5" stroke="#0A3B25" stroke-width="1"/></g>
   <?php foreach ( $shots as $sh ) : $cx = (float) $sh['y']; $cy = max( 0.5, 105 - (float) $sh['x'] ); if ( $cy > 35 ) $cy = 35;
	$r = 0.8 + 3.2 * sqrt( max( 0.01, (float) $sh['xg'] ) );
	$fill = $sh['goal'] ? '#e3c567' : ( $sh['on'] ? '#0A3B25' : '#ffffff' ); ?>
    <circle cx="<?php echo round( $cx, 2 ); ?>" cy="<?php echo round( $cy, 2 ); ?>" r="<?php echo round( $r, 2 ); ?>" fill="<?php echo $fill; ?>" fill-opacity="<?php echo $sh['goal'] ? 1 : .85; ?>" stroke="#0A3B25" stroke-width=".35"><title><?php echo $e( $sh['min'] . "' · " . number_format( (float) $sh['xg'], 2 ) . ' xG' . ( $sh['goal'] ? ' · Goal' : '' ) ); ?></title></circle>
   <?php endforeach; ?>
  </svg>
 </div>
 <?php endif; ?>
</section>
<?php endif; ?>

<section class="fsp-sec" id="fsp-log">
 <h2 class="fsp-h2">Match log</h2><div class="fsp-rule"></div>
 <?php $played = array_values( array_filter( $matches, function ( $m ) { return ! empty( $m['played'] ) || ! empty( $m['bench'] ); } ) ); ?>
 <?php if ( ! $played ) : ?><p class="fsp-sub" style="margin:0">No matches yet this season.</p><?php else : ?>
 <div class="fsp-card fsp-tw"><table class="fsp-t" style="min-width:640px"><thead><tr><th class="l">Date</th><th class="l">Match</th><th>Result</th><th>Mins</th><?php if ( ! $gk ) : ?><th>G</th><th>A</th><?php endif; ?><th>Cards</th><th>Rating</th></tr></thead><tbody>
 <?php foreach ( $played as $m ) : $r = $m['rating']; ?>
  <tr><td class="l" style="white-space:nowrap"><?php echo $e( fsp_fdate( $m['date'], 'j M' ) ); ?></td>
   <td class="l"><b><?php echo $e( ( $m['home'] ? 'v ' : 'at ' ) . $m['opp'] ); ?></b><?php if ( $m['team'] !== $h['club'] ) echo '<span class="fsp-intl">' . $e( $m['team'] ) . '</span>'; ?><br><span style="font-size:11px;color:#767676"><?php echo $e( fsp_comp_short( $m['comp'] ) ); ?></span></td>
   <td><?php if ( $m['res'] ) : ?><span class="fsp-res fsp-<?php echo $e( $m['res'] ); ?>"><?php echo $e( $m['res'] . ' ' . $m['gf'] . '-' . $m['ga'] ); ?></span><?php endif; ?></td>
   <td><?php echo ! empty( $m['played'] ) ? (int) $m['mins'] . "'" : '<span style="color:#999;font-size:11px">Unused</span>'; ?></td>
   <?php if ( ! $gk ) : ?><td class="<?php echo $m['g'] ? '' : 'z'; ?>"><?php echo (int) $m['g']; ?></td><td class="<?php echo $m['a'] ? '' : 'z'; ?>"><?php echo (int) $m['a']; ?></td><?php endif; ?>
   <td><?php echo str_repeat( '<span class="fsp-card-y"></span> ', (int) $m['yc'] ) . str_repeat( '<span class="fsp-card-r"></span>', (int) $m['rc'] ); ?></td>
   <td><?php if ( $r ) : ?><span class="fsp-rt<?php echo $r >= 7.5 ? ' top' : ( $r >= 7 ? ' hi' : '' ); ?>"><?php echo $e( number_format( $r, 1 ) ); ?></span><?php echo ! empty( $m['potm'] ) ? ' <span title="Player of the match" style="color:#c9a227">★</span>' : ''; ?><?php else : ?>–<?php endif; ?></td></tr>
 <?php endforeach; ?></tbody></table></div>
 <?php endif; ?>
</section>

<section class="fsp-sec" id="fsp-career">
 <h2 class="fsp-h2">Career</h2><div class="fsp-rule"></div>
 <div class="fsp-g2e">
  <div>
   <?php if ( ! empty( $h['career'] ) ) : ?>
   <div class="fsp-card fsp-car"><div class="row hd"><span>Years</span><span>Club</span><span class="n">Apps</span><span class="n">Goals</span></div>
   <?php foreach ( $h['career'] as $c ) : ?>
    <div class="row"><span class="yr"><?php echo $e( $c['from'] . '–' . ( $c['to'] ?: 'now' ) ); ?></span><span class="tm"><?php echo $e( $c['team'] ); ?><?php echo $c['loan'] ? '<small>Loan</small>' : ''; ?></span><span class="n"><?php echo $e( $c['apps'] ?: '–' ); ?></span><span class="n"><?php echo $e( $c['goals'] ?: '–' ); ?></span></div>
   <?php endforeach; ?></div>
   <?php endif; ?>
   <?php if ( ! empty( $h['intl'] ) ) : ?>
   <div class="fsp-card fsp-car" style="margin-top:16px"><div class="row hd"><span>Years</span><span>International</span><span class="n">Caps</span><span class="n">Goals</span></div>
   <?php foreach ( $h['intl'] as $c ) : ?>
    <div class="row"><span class="yr"><?php echo $e( $c['from'] . '–' . ( $c['to'] ?: 'now' ) ); ?></span><span class="tm"><?php echo $e( $c['team'] ); ?></span><span class="n"><?php echo $e( $c['apps'] ?: '–' ); ?></span><span class="n"><?php echo $e( $c['goals'] ?: '–' ); ?></span></div>
   <?php endforeach; ?></div>
   <?php endif; ?>
  </div>
  <div>
   <?php if ( ! empty( $h['trophies'] ) ) : ?>
   <div class="fsp-card fsp-tro"><h4 style="margin:4px 0 8px;font-size:13px;font-weight:900;letter-spacing:1px;text-transform:uppercase;color:#111">Honours</h4>
   <?php foreach ( $h['trophies'] as $t ) : ?>
    <div><span class="cup">🏆</span><b><?php echo $e( $t['comp'] ); ?></b><span class="w"><?php echo $e( $t['team'] ); ?> · <?php echo $e( implode( ', ', array_map( function ( $x ) { return str_replace( '/20', '/', $x ); }, $t['won'] ) ) ); ?></span></div>
   <?php endforeach; ?></div>
   <?php endif; ?>
  </div>
 </div>
 <div class="fsp-src">Name in Thai, date of birth, nationality and photo: Thai League registration. Match data, ratings and stats: FotMob (Opta). Updated <?php echo $e( $h['updated'] ); ?> Bangkok time.</div>
</section>
</div>

<script>
(function(){
 var root=document.querySelector('.fsp');if(!root)return;
 root.querySelectorAll('.fsp-tog button').forEach(function(b){b.addEventListener('click',function(){
  var per=b.getAttribute('data-m')==='p';root.querySelectorAll('.fsp-tog button').forEach(function(x){x.classList.toggle('on',x===b);});
  root.querySelectorAll('.fsp-st').forEach(function(s){var v=s.querySelector('.v'),i=s.querySelector('.bar i'),pc=s.querySelector('.pc');
   v.textContent=per?s.getAttribute('data-p'):s.getAttribute('data-t');
   var p=s.getAttribute(per?'data-pp':'data-tp');if(i&&p!==''){i.style.width=Math.max(2,+p)+'%';i.classList.toggle('hi',+p>=80);if(pc)pc.textContent=p+'th percentile';}});});});
 var links=root.querySelectorAll('.fsp-nav a'),secs=[].map.call(links,function(a){return document.querySelector(a.getAttribute('href'));});
 window.addEventListener('scroll',function(){var y=window.scrollY+90,on=0;secs.forEach(function(s,i){if(s&&s.offsetTop<=y)on=i;});links.forEach(function(a,i){a.classList.toggle('on',i===on);});},{passive:true});
})();
</script>
</div>
	<?php
	return ob_get_clean();
}

add_filter( 'template_include', function ( $tpl ) {
	if ( is_admin() || ! is_singular( 'player' ) || isset( $_GET['fs_oldplayer'] ) ) return $tpl;
	$d = fsp_data( get_queried_object_id() );
	if ( ! $d ) return $tpl;
	get_header();
	echo fsp_render( $d );
	get_footer();
	exit;
}, 999 );

}
