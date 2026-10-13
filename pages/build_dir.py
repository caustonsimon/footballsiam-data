# -*- coding: utf-8 -*-
import json,re,html,datetime
P='/home/claude/t2/players/'
v=json.load(open(P+'verified_all.json'))
ft=json.load(open(P+'full_thai_abroad.json'))
DROP={'watts','eklund','paschke'}
E=lambda s: html.escape(str(s or ''),quote=True)
def age(b):
    if not b: return None
    try:
        y=int(str(b)[:4])
    except: return None
    if len(str(b))>=10:
        d=datetime.date.fromisoformat(str(b)[:10]); t=datetime.date(2026,10,13)
        return t.year-d.year-((t.month,t.day)<(d.month,d.day))
    return None
def senior(p):
    s=(p.get('thailand_status') or '').lower()
    return s.startswith('senior') or 'senior international' in s or 'senior cap' in s
def youth(p):
    s=(p.get('thailand_status') or '').lower()
    return bool(re.search(r'u1\d|u2\d|youth',s)) and not senior(p)
order={'pro':1,'semi-pro':2,'college':3,'academy':4,'amateur':5}
half=[p for p in v if p.get('include') and p['key'] not in DROP]
for p in half:
    p['_g']='int' if senior(p) else ('youth' if youth(p) else (p.get('tier') or 'amateur'))
half.sort(key=lambda p:(0 if p['_g']=='int' else 1, order.get(p.get('tier'),6), p['name'].split()[-1]))
def src_links(srcs):
    out=[];seen=set()
    for u in (srcs or [])[:6]:
        dom=re.sub(r'^https?://(www\.)?','',u).split('/')[0]
        lab={'instagram.com':'Instagram','transfermarkt.com':'Transfermarkt','transfermarkt.co.uk':'Transfermarkt','fotmob.com':'FotMob','en.wikipedia.org':'Wikipedia','th.wikipedia.org':'Wikipedia (Thai)','int.soccerway.com':'Soccerway','soccerway.com':'Soccerway'}.get(dom,dom)
        if lab in seen: continue
        seen.add(lab)
        out.append('<a href="%s" rel="nofollow noopener" target="_blank">%s</a>'%(E(u),E(lab)))
    return ', '.join(out)
def tag_for(p):
    g=p['_g']
    return {'int':('Thailand international','gold'),'youth':('Thailand youth','green'),'pro':('Professional',''),'semi-pro':('Semi-pro',''),'academy':('Academy',''),'amateur':('Amateur',''),'college':('College','')}.get(g,('',''))
cards=[]
for p in half:
    t,cls=tag_for(p)
    a=age(p.get('born')); born=str(p.get('born') or '')
    bl=('Born %s'%born[:4]) if born else ''
    if a: bl='Age %d'%a
    meta=' &middot; '.join(x for x in [E((p.get('position') or '').split('(')[0].strip()), E(bl)] if x)
    club=E(p.get('club') or 'Club unconfirmed'); lvl=p.get('team_level') or ''
    lvl=E(re.sub(r'\s*\(.*?\)','',lvl)) if lvl and lvl.lower() not in ('first team','senior') else ''
    where=E(p.get('country') or '')
    unc='<span class="dx-unc">Details from social media, unconfirmed</span>' if p.get('confidence')=='low' else ''
    cards.append(
     '<div class="dx-card" id="p-%s" data-g="%s" data-c="%s" data-n="%s">'%(E(p['key']),p['_g'],E((p.get('country') or '').lower()),E((p['name']+' '+(p.get('club') or '')).lower()))+
     '<div class="dx-top"><span class="dx-tag %s">%s</span><span class="dx-where">%s</span></div>'%(cls,t,where)+
     '<h3 class="dx-name">%s</h3>'%E(re.sub(r'\s*\(.*?\)','',p['name']))+
     '<div class="dx-club">%s%s</div>'%(club,(' <span>'+lvl+'</span>') if lvl else '')+
     '<div class="dx-meta">%s</div>'%meta+
     '<dl class="dx-dl"><dt>Thai link</dt><dd>%s</dd><dt>Thailand</dt><dd>%s</dd>%s</dl>'%(E(p.get('thai_link') or 'Thai parent'),E(p.get('thailand_status') or 'Eligible, not yet called up'),('<dt>Also</dt><dd>%s</dd>'%E(p['other_intl'])) if p.get('other_intl') else '')+
     '<p class="dx-bio">%s</p>%s'%(E(p.get('bio')),unc)+
     '<div class="dx-src">Sources: %s</div></div>'%src_links(p.get('sources')))
# full Thai
main=[];low=[]
for p in ft:
    b=p.get('bio') or ''
    if b.startswith('[Amateur') or b.startswith('[Parentage') : low.append(p); continue
    main.append(p)
fcards=[]
for p in main:
    b=re.sub(r'^\[[^\]]*\]\s*','',p.get('bio') or '')
    tm='Transfermarkt-only' in (p.get('bio') or '')
    meta=' &middot; '.join(x for x in [E(p.get('position')), ('Age %d'%age(p['dob'])) if age(p.get('dob')) else ''] if x)
    fcards.append('<div class="dx-card" data-g="ft" data-c="%s" data-n="%s"><div class="dx-top"><span class="dx-tag">%s</span><span class="dx-where">%s</span></div><h3 class="dx-name">%s</h3><div class="dx-club">%s%s</div><div class="dx-meta">%s</div><dl class="dx-dl"><dt>League</dt><dd>%s</dd><dt>Thailand</dt><dd>%s</dd></dl><p class="dx-bio">%s</p>%s<div class="dx-src">Sources: %s</div></div>'%(
      E((p.get('country') or '').lower()),E((p['name']+' '+(p.get('club') or '')).lower()),'On loan' if p.get('loan_from') else 'Thai player abroad',E(p.get('country')),E(p['name']),E(p.get('club')),(' <span>on loan from %s</span>'%E(p['loan_from'])) if p.get('loan_from') else '',meta,E(p.get('league') or ''),E(p.get('thailand') or 'Uncapped'),E(b),'<span class="dx-unc">Listed on Transfermarkt; not yet confirmed elsewhere</span>' if tm else '',src_links(p.get('sources'))))
rows=''.join('<tr><td><b>%s</b></td><td>%s</td><td>%s</td></tr>'%(E(p['name']),E(p.get('club')),E(p.get('country'))) for p in low)
nint=sum(1 for p in half if p['_g']=='int'); nyouth=sum(1 for p in half if p['_g']=='youth')
ncountries=len({p.get('country') for p in half if p.get('country') and p.get('country')!='Thailand'})
CSS=open('/home/claude/t2/dir/dir.css').read()
JS=open('/home/claude/t2/dir/dir.js').read()
chips=''.join('<button class="dx-chip%s" data-f="%s">%s</button>'%(' on' if f=='all' else '',f,l) for f,l in [('all','All'),('int','Thailand internationals'),('youth','Thailand youth'),('pro','Professional'),('semi-pro','Semi-pro'),('academy','Academy'),('amateur','Amateur')])
body=(
'<div class="dx">'
'<span class="dx-kick">Player directory &middot; Updated 13 October 2026</span>'
'<p class="dx-stand">Every half-Thai footballer we can find, at home and abroad, and every Thai player registered with a club outside Thailand. Men only, aged 15 and over.</p>'
'<div class="dx-stats"><div><b>%d</b><span>half-Thai players</span></div><div class="g"><b>%d</b><span>senior Thailand internationals</span></div><div><b>%d</b><span>in Thailand youth squads</span></div><div><b>%d</b><span>countries outside Thailand</span></div></div>'%(len(half),nint,nyouth,ncountries)+
'<div class="dx-tabs"><button class="dx-tab on" data-t="half">Half-Thai players (%d)</button><button class="dx-tab" data-t="ft">Thai players abroad (%d)</button></div>'%(len(half),len(main)+len(low))+
'<div class="dx-pane on" data-p="half"><p class="dx-intro">Players with a Thai parent (luk khrueng), plus a few with a Thai grandparent, marked as such. Many are eligible for Thailand; some have already chosen another country. Read the argument in <a href="https://footballsiam.com/thailand-diaspora-luk-khrueng-players/">Thailand looks for Thai mothers. Indonesia looks everywhere</a>.</p>'
'<div class="dx-bar"><div class="dx-chips">%s</div><input class="dx-q" type="search" placeholder="Search name or club" aria-label="Search players"></div>'%chips+
'<div class="dx-grid" id="dx-half">%s</div></div>'%''.join(cards)+
'<div class="dx-pane" data-p="ft"><p class="dx-intro">Players with two Thai parents registered with clubs outside Thailand in 2026/27.</p><div class="dx-grid">%s</div>'%''.join(fcards)+
'<h2 class="dx-h">Also registered abroad, lower leagues</h2><p class="dx-intro">Listed by Transfermarkt with Thai citizenship at amateur and regional clubs. We have not confirmed these elsewhere, and some listings may be out of date.</p><div class="dx-tw"><table class="dx-t"><tr><th>Player</th><th>Club</th><th>Country</th></tr>%s</table></div></div>'%rows+
'<p class="dx-end">Compiled by Football Siam from <a href="https://www.instagram.com/half_thailand.123/" rel="nofollow noopener" target="_blank">@half_thailand.123</a>, which follows half-Thai players around the world, checked against Transfermarkt, club and federation sites, FotMob and Thai and local press. Entries marked unconfirmed rest on social media alone. Know a player we have missed, or spotted an error? Message us on LINE or email the editor.</p>'
'</div>')
out='<style>%s</style>%s<script>%s</script>'%(' '.join(CSS.split()),body,' '.join(JS.split()))
open('/home/claude/t2/dir/directory.html','w').write(out)
print(len(out),len(half),nint,nyouth,ncountries,len(main),len(low))
