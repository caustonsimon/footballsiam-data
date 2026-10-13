# -*- coding: utf-8 -*-
import json,re,datetime
P='/home/claude/t2/players/'
v=json.load(open(P+'verified_all.json'))
ft=json.load(open(P+'full_thai_abroad.json'))
DROP={'watts','eklund','paschke'}
T=datetime.date(2026,10,13)
def age(b):
    b=str(b or '')
    if len(b)>=10:
        try:
            d=datetime.date.fromisoformat(b[:10]); return T.year-d.year-((T.month,T.day)<(d.month,d.day))
        except: return None
    return None
def yr(b):
    m=re.match(r'(\d{4})',str(b or '')); return m.group(1) if m else ''
NORM={'United States':'USA','US':'USA','England':'England','UK':'England','Korea':'South Korea','Republic of Korea':'South Korea'}
def cty(c):
    c=(c or '').strip(); return NORM.get(c,c)
def clean(s): return re.sub(r'\s*\(.*?\)','',s or '').strip()
def lvl(p):
    s=(p.get('thailand_status') or '').lower()
    if s.startswith('senior') or 'senior international' in s: return 'Thailand international'
    if re.search(r'u1\d|u2\d|youth',s): return 'Thailand youth'
    return {'pro':'Professional','semi-pro':'Semi-pro','academy':'Academy','amateur':'Amateur','college':'College'}.get(p.get('tier'),'')
def srcs(lst):
    out=[];seen=set()
    for u in (lst or [])[:6]:
        dom=re.sub(r'^https?://(www\.)?','',u).split('/')[0]
        lab={'instagram.com':'Instagram','transfermarkt.com':'Transfermarkt','transfermarkt.co.uk':'Transfermarkt','fotmob.com':'FotMob','en.wikipedia.org':'Wikipedia'}.get(dom,dom)
        if lab in seen: continue
        seen.add(lab); out.append({'u':u,'l':lab})
    return out
rows=[]
for p in v:
    if not p.get('include') or p['key'] in DROP: continue
    a=age(p.get('born'))
    rows.append({'k':p['key'],'n':clean(p['name']),'cat':'dual','c':cty(p.get('country')),'club':p.get('club') or 'Club unconfirmed',
      'tl':clean(p.get('team_level')) if (p.get('team_level') or '').lower() not in ('first team','senior','') else '',
      'pos':clean(p.get('position')),'age':('Age %d'%a) if a else (('Born '+yr(p.get('born'))) if yr(p.get('born')) else ''),
      'lv':lvl(p),'her':p.get('thai_link') or 'Thai parent','th':p.get('thailand_status') or 'Eligible, not yet called up',
      'oi':p.get('other_intl') or '','bio':p.get('bio') or '','s':srcs(p.get('sources')),'unc':p.get('confidence')=='low'})
# Duchowny brothers (added 13 Oct 2026 at Simon's request; Transfermarkt checked)
rows.append({'k':'jonah-duchowny','n':'Jonah Duchowny','cat':'dual','c':'Thailand','club':'Pattaya United','tl':'','pos':'Attacking midfielder','age':'Age 21',
  'lv':'Thailand youth','her':'Thai parent; also eligible for the United States','th':'Thailand U20 (2 caps)','oi':'',
  'bio':'Jonah Duchowny is an attacking midfielder who made his name with Los Cabos United in Mexico, scoring eight goals with seven assists in 2025/26, before joining Pattaya United in Thai League 2 in July 2026. He has two caps for Thailand U20 and is also eligible for the United States.',
  's':[{'u':'https://www.transfermarkt.com/jonah-duchowny/profil/spieler/1167018','l':'Transfermarkt'},{'u':'https://footballsiam.com/the-ones-that-got-away-thailand-diaspora/','l':'Football Siam'}],'unc':False})
rows.append({'k':'micah-duchowny','n':'Micah Duchowny','cat':'dual','c':'Mexico','club':'Los Cabos United','tl':'','pos':'Right-back','age':'Age 23',
  'lv':'Thailand youth','her':'Thai parent; brother of Jonah Duchowny','th':'Thailand youth international','oi':'',
  'bio':'Micah Duchowny is a right-back, who can also play centre-back, at Los Cabos United in Mexico, where he joined in August 2025. He is the older brother of Jonah Duchowny, holds Thai citizenship and, like his brother, has played for Thailand at youth level.',
  's':[{'u':'https://www.transfermarkt.com/micah-duchowny/profil/spieler/1167019','l':'Transfermarkt'}],'unc':False})
rows.append({'k':'yapp','n':'Siam Yapp','cat':'dual','c':'Thailand','club':'Police Tero','tl':'','pos':'Left winger','age':'Age 22',
  'lv':'Thailand youth','her':'Thai parent','th':'Thailand U23 (2 caps)','oi':'',
  'bio':'Siam Yapp is a Phuket-born left winger who joined Police Tero in Thai League 2 in August 2026. He has two caps for Thailand at under-23 level.',
  's':[{'u':'https://www.transfermarkt.com/siam-yapp/profil/spieler/971138','l':'Transfermarkt'}],'unc':False})
for p in ft:
    b=p.get('bio') or ''
    low=b.startswith('[Amateur') or b.startswith('[Parentage')
    tm='Transfermarkt-only' in b
    b=re.sub(r'^\[[^\]]*\]\s*','',b)
    a=age(p.get('dob'))
    rows.append({'k':'ta-'+re.sub(r'[^a-z]+','-',p['name'].lower()).strip('-'),'n':p['name'],'cat':'abroad','c':cty(p.get('country')),'club':clean(p.get('club')),
      'tl':('On loan from '+p['loan_from']) if p.get('loan_from') else '','pos':p.get('position') or '','age':('Age %d'%a) if a else '',
      'lv':'Lower leagues' if low else ('On loan' if p.get('loan_from') else 'Professional'),'her':'Thai','th':p.get('thailand') or 'Uncapped',
      'oi':'','bio':b,'s':srcs(p.get('sources')),'unc':low or tm,'lg':p.get('league') or ''})
GEO={'Sweden':(62.0,15.5),'Thailand':(15.0,101.0),'Germany':(51.2,10.4),'Norway':(61.0,9.0),'England':(52.6,-1.5),'Scotland':(56.6,-4.2),'Wales':(52.3,-3.7),
'Switzerland':(46.8,8.2),'Netherlands':(52.2,5.5),'USA':(39.5,-98.5),'Denmark':(56.0,9.5),'Australia':(-25.5,134.5),'Belgium':(50.6,4.6),'France':(46.5,2.4),
'Spain':(40.2,-3.6),'Japan':(36.5,138.5),'Croatia':(45.3,15.7),'Hong Kong':(22.35,114.15),'Qatar':(25.3,51.2),'Canada':(56.0,-100.0),'Luxembourg':(49.7,6.1),
'Mexico':(23.5,-102.5),'Singapore':(1.35,103.82),'South Korea':(36.3,127.8),'Laos':(18.5,103.0),'Austria':(47.6,14.1),'Ireland':(53.2,-8.0),
'Faroe Islands':(62.0,-6.9),'Malta':(35.9,14.4),'Iceland':(64.9,-18.6),'Morocco':(31.8,-7.1),'Finland':(64.5,26.0),'Portugal':(39.6,-8.0),'Italy':(42.8,12.5)}
miss={r['c'] for r in rows if r['c'] not in GEO}
print('missing geo',miss)
IMGS={'skogmar':'https://footballsiam.com/wp-content/uploads/2026/10/skogmar.jpeg','marinc':'https://footballsiam.com/wp-content/uploads/2026/10/Apolo-Marinch.jpg','mexes':'https://footballsiam.com/wp-content/uploads/2026/10/Silva-Mexes.jpg'}
for r in rows:
    if r['k'] in IMGS: r['img']=IMGS[r['k']]
    if r['k']=='mexes':
        r['bio']=r['bio'].rstrip()+' He is the son of the former Wales striker Robert Earnshaw, which also makes him eligible for Wales and Zambia.'
        r['oi']='Also eligible for Wales and Zambia (not capped)'
rows=[r for r in rows if r['c'] in GEO]
order={'Thailand international':0,'Thailand youth':1,'Professional':2,'On loan':2,'Semi-pro':3,'College':4,'Academy':5,'Amateur':6,'Lower leagues':7}
rows.sort(key=lambda r:(order.get(r['lv'],8),r['n'].split()[-1]))
for r in rows:
    if r['cat']=='dual': r['g']={'Thailand international':'int','Thailand youth':'yth'}.get(r['lv'],'elig')
    else: r['g']='low' if r['lv']=='Lower leagues' else 'pro'
CSS=open('/home/claude/t2/map/map.css').read(); JS=open('/home/claude/t2/map/map.js').read()
DUAL='https://footballsiam.com/dual-heritage-players/'; ABR='https://footballsiam.com/thai-overseas-players/'
def build(mode,out):
    rs=[r for r in rows if r['cat']==mode]
    geo={c:GEO[c] for c in {r['c'] for r in rs}}
    nint=sum(r['g']=='int' for r in rs); nyth=sum(r['g']=='yth' for r in rs); nc=len({r['c'] for r in rs if r['c']!='Thailand'})
    if mode=='dual':
        kick='Dual heritage players'
        stand='Footballers with a Thai parent, known in Thailand as <em>luk khrueng</em>, and where they play. Some already wear the shirt; many more are eligible. Men, aged 15 and over. Tap a country to see who is there.'
        stats='<div><b>%d</b><span>dual heritage players</span></div><div class="g"><b>%d</b><span>Thailand internationals</span></div><div><b>%d</b><span>in Thailand youth squads</span></div><div><b>%d</b><span>countries outside Thailand</span></div>'%(len(rs),nint,nyth,nc)
        chips='<button class="mx-chip on" data-f="all">All</button><button class="mx-chip" data-f="int">Thailand internationals</button><button class="mx-chip" data-f="yth">Thailand youth</button><button class="mx-chip" data-f="elig">Eligible, not yet capped</button>'
        cross='<div class="mx-x">Looking for Thai players at clubs abroad? <a href="%s">See Thais Abroad</a>.</div>'%ABR
        reads='Read <a href="https://footballsiam.com/thailand-diaspora-luk-khrueng-players/">Twenty to call</a>, our list of who Thailand should be calling, and <a href="https://footballsiam.com/the-ones-that-got-away-thailand-diaspora/">The ones that got away</a>.'
        start='Sweden'
    else:
        kick='Thais abroad'
        stand='Thai footballers registered with clubs outside Thailand in 2026/27, from the J.League to the Lao League. Men, aged 15 and over. Tap a country to see who is there.'
        npro=sum(r['g']=='pro' for r in rs)
        stats='<div><b>%d</b><span>Thai players abroad</span></div><div class="g"><b>%d</b><span>at professional or academy level</span></div><div><b>%d</b><span>countries</span></div><div><b>%d</b><span>in Japan</span></div>'%(len(rs),npro,len({r['c'] for r in rs}),sum(r['c']=='Japan' for r in rs))
        chips='<button class="mx-chip on" data-f="all">All</button><button class="mx-chip" data-f="pro">Professional and academy</button><button class="mx-chip" data-f="low">Lower leagues</button>'
        cross='<div class="mx-x">Players with a Thai parent playing abroad are on our <a href="%s">Dual Heritage map</a>.</div>'%DUAL
        reads='Lower-league entries come from Transfermarkt and have not been confirmed elsewhere.'
        start='Japan'
    body=('<div class="mx" id="mx" data-start="%s">'%start+
    '<span class="mx-kick">%s &middot; Updated 13 October 2026</span>'%kick+
    '<p class="mx-stand">%s</p>'%stand+cross+
    '<div class="mx-stats">%s</div>'%stats+
    '<div class="mx-bar"><div class="mx-chips">%s</div><input class="mx-q" type="search" placeholder="Find a player or club" aria-label="Find a player or club"><div class="mx-res"></div></div>'%chips+
    '<div class="mx-wrap"><div id="mx-map" class="mx-map" role="application" aria-label="World map of players"></div><div class="mx-panel"><div class="mx-ph">Tap a country on the map</div><div class="mx-top"></div><div class="mx-list"></div></div></div>'
    '<p class="mx-end">Compiled by Football Siam from <a href="https://www.instagram.com/half_thailand.123/" rel="nofollow noopener" target="_blank">@half_thailand.123</a>, which follows dual heritage Thai players around the world, and from Transfermarkt, club and federation sites, FotMob and the press. Entries marked unconfirmed rest on one source. %s Spotted a player we have missed? Message us on LINE.</p>'%reads+
    '</div>')
    import base64
    d64=base64.b64encode(json.dumps({'rows':rs,'geo':geo},ensure_ascii=False).encode('utf-8')).decode()
    j64=base64.b64encode(' '.join(JS.split('\n')).encode('ascii')).decode()
    html=('<!-- wp:html --><link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css">'
     '<style>%s</style>%s<div id="mx-data" hidden data-d="%s"></div>'
     '<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script><script>eval(atob("%s"))</script><!-- /wp:html -->')%(
     ' '.join(CSS.split()),body,d64,j64)
    open(out,'w').write(html); print(mode,len(html),len(rs))
build('dual','/home/claude/t2/map/dual.html'); build('abroad','/home/claude/t2/map/abroad.html')
