(function(){ function start(){ var root=document.getElementById('mx'); if(!root||!window.L) return;
var D=JSON.parse(new TextDecoder('utf-8').decode(Uint8Array.from(atob(document.getElementById('mx-data').getAttribute('data-d')),function(c){return c.charCodeAt(0)}))), R=D.rows, G=D.geo, U=D.ui||{en:{},th:{}};
var f='all', sel=null, markers={}, lang='en';
try{var qs=(location.search.match(/[?&]lang=(en|th)/)||[])[1]; var st=null; try{st=localStorage.getItem('fs-lang')}catch(e){} lang=qs||st||'en'; if(lang!=='th'){lang='en'}}catch(e){lang='en'}
function u(k){var t=U[lang]||{}; return (t[k]!==undefined)?t[k]:((U.en||{})[k]!==undefined?U.en[k]:k)}
function cn(k){return lang==='th'&&D.cn&&D.cn[k]?D.cn[k]:k}
function tx(r,k){if(lang==='th'&&r.t&&r.t[k]){return r.t[k]} return r[k]||''}
function lv(v){return lang==='th'&&D.lv&&D.lv[v]?D.lv[v]:v}
function ag(a){if(lang!=='th'||!a){return a} var m=String(a).match(/^Age (\d+)$/); if(m){return u('age').replace('%d',m[1])} m=String(a).match(/^Born (\d{4})$/); if(m){return u('born').replace('%d',m[1])} return a}
var map=L.map('mx-map',{worldCopyJump:true,scrollWheelZoom:false,zoomSnap:0.5}).setView([38,25],2.5);
var RG={'World':[[-45,-130],[70,150]],'Europe':[[35,-12],[68,32]],'Asia':[[-12,90],[45,145]],'Americas':[[10,-125],[55,-65]]}; var rgBox=null;
var rc=L.control({position:'topright'});
rc.onAdd=function(){var d=L.DomUtil.create('div','mx-rg'); rgBox=d; d.innerHTML=Object.keys(RG).map(function(k){return '<button data-r="'+k+'"></button>'}).join(''); L.DomEvent.disableClickPropagation(d); d.querySelectorAll('button').forEach(function(b){b.onclick=function(){map.flyToBounds(RG[b.getAttribute('data-r')],{duration:0.6})}}); return d};
rc.addTo(map);
L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}',{attribution:'Tiles &copy; Esri, HERE, Garmin, OpenStreetMap contributors',maxZoom:10}).addTo(map);
function vis(r){return f==='all'||r.g===f}
function esc(s){return String(s||'').replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]})}
function counts(){var c={};R.forEach(function(r){if(vis(r)){c[r.c]=(c[r.c]||0)+1}});return c}
function draw(){ Object.keys(markers).forEach(function(k){map.removeLayer(markers[k])}); markers={};
 var c=counts();
 Object.keys(c).forEach(function(k){var n=c[k], s=Math.round(22+Math.min(22,Math.sqrt(n)*4));
  var ic=L.divIcon({className:'',html:'<div class="mx-pin'+(k===sel?' sel':'')+'" style="width:'+s+'px;height:'+s+'px;font-size:'+(s>36?14:11)+'px">'+n+'</div>',iconSize:[s,s],iconAnchor:[s/2,s/2]});
  var m=L.marker(G[k],{icon:ic,title:cn(k)+': '+n,keyboard:true}).addTo(map); m.bindTooltip(esc(cn(k))+' &middot; '+n,{direction:'top',offset:[0,-s/2]}); m.on('click',function(){pick(k)}); markers[k]=m; });
 var top=root.querySelector('.mx-top'); var arr=Object.keys(c).sort(function(a,b){return c[b]-c[a]});
 top.innerHTML=arr.map(function(k){return '<button data-c="'+esc(k)+'">'+esc(cn(k))+' <b>'+c[k]+'</b></button>'}).join('');
 top.querySelectorAll('button').forEach(function(b){b.onclick=function(){pick(b.getAttribute('data-c'),true)}});
}
function ini(n){var p=String(n).split(' ').filter(function(x){return x});return ((p.length?p.slice(0,1).join(''):'').charAt(0)+(p.length>1?p.slice(-1).join('').charAt(0):'')).toUpperCase()}
function tagc(r){return r.lv==='Thailand international'?'int':(r.lv==='Thailand youth'?'yth':(r.cat==='abroad'?'ab':''))}
function rowHtml(r){ var her=r.cat==='abroad'?(lang==='th'?u('thai'):'Thai'):tx(r,'her'); var oi=tx(r,'oi'), lg=tx(r,'lg');
 var dl='<dt>'+esc(r.cat==='abroad'?u('dt_player'):u('dt_her'))+'</dt><dd>'+esc(her)+'</dd><dt>'+esc(u('dt_th'))+'</dt><dd>'+esc(tx(r,'th'))+'</dd>'+(oi?'<dt>'+esc(u('dt_also'))+'</dt><dd>'+esc(oi)+'</dd>':'')+(lg?'<dt>'+esc(u('dt_lg'))+'</dt><dd>'+esc(lg)+'</dd>':'');
 var s=(r.s||[]).map(function(x){return '<a href="'+esc(x.u)+'" target="_blank" rel="nofollow noopener">'+esc(x.l)+'</a>'}).join(', ');
 var tl=tx(r,'tl'), pos=tx(r,'pos'), age=ag(r.age); var nt=(lang==='th'&&r.t&&r.t.n)?'<span class="mx-nt">'+esc(r.t.n)+'</span>':'';
 var tag=r.cat==='abroad'&&r.lv==='Professional'?(lang==='th'?u('tag_ab'):'Thai abroad'):lv(r.lv);
 return '<div class="mx-row" id="p-'+esc(r.k)+'"><button class="mx-rh" aria-expanded="false"><span class="mx-av">'+(r.img?'<img src="'+esc(r.img)+'" alt="">':esc(ini(r.n)))+'</span><span><span class="mx-nm">'+esc(r.n)+'</span>'+nt+'<span class="mx-cl">'+esc(r.club)+(tl?' &middot; '+esc(tl):'')+(pos?' &middot; '+esc(pos):'')+(age?' &middot; '+esc(age):'')+'</span></span><span class="mx-tag '+tagc(r)+'">'+esc(tag)+'</span></button><div class="mx-det"><dl>'+dl+'</dl><p>'+esc(tx(r,'bio'))+'</p>'+(r.unc?'<div class="u">'+esc(u('unc'))+'</div>':'')+(s?'<div class="s">'+esc(u('src'))+' '+s+'</div>':'')+'</div></div>';
}
function head(k,list){ var nd=list.filter(function(r){return r.cat==='dual'}).length, na=list.length-nd;
 var n=lang==='th'?u('n_players').replace('%d',list.length):(list.length+' player'+(list.length===1?'':'s'));
 var mix=(nd&&na)?' &middot; '+(lang==='th'?u('n_mix').replace('%a',nd).replace('%b',na):(nd+' dual heritage, '+na+' Thai')):'';
 return esc(cn(k))+'<small>'+n+mix+'</small>'}
function pick(k,fly,open){ sel=k; draw();
 if(fly){map.flyTo(G[k],k==='Thailand'||k==='Laos'||k==='Singapore'?5:4,{duration:0.6})}
 var list=R.filter(function(r){return r.c===k&&vis(r)});
 root.querySelector('.mx-ph').innerHTML=head(k,list);
 var L2=root.querySelector('.mx-list'); L2.innerHTML=list.map(rowHtml).join('');
 L2.querySelectorAll('.mx-rh').forEach(function(b){b.onclick=function(){var row=b.parentNode;var o=row.classList.toggle('open');b.setAttribute('aria-expanded',o?'true':'false')}});
 L2.scrollTop=0;
 if(open){var el=document.getElementById('p-'+open); if(el){el.classList.add('open'); el.querySelector('.mx-rh').setAttribute('aria-expanded','true'); setTimeout(function(){L2.scrollTop=el.offsetTop-L2.offsetTop-6; if(window.innerWidth<900){el.scrollIntoView({block:'center'})}},250)}}
}
function setLang(l,keep){ lang=l==='th'?'th':'en'; root.setAttribute('data-lang',lang); root.setAttribute('lang',lang);
 try{localStorage.setItem('fs-lang',lang)}catch(e){}
 root.querySelectorAll('.mx-lang button').forEach(function(b){var on=b.getAttribute('data-lang')===lang; b.classList.toggle('on',on); b.setAttribute('aria-selected',on?'true':'false')});
 root.querySelectorAll('[data-i]').forEach(function(el){var k=el.getAttribute('data-i'); var v=u(k); if(v!==k){el.innerHTML=v}});
 q.setAttribute('placeholder',u('find')); q.setAttribute('aria-label',u('find'));
 if(rgBox){rgBox.querySelectorAll('button').forEach(function(b){b.textContent=u('rg_'+b.getAttribute('data-r'))})}
 var op=root.querySelector('.mx-row.open'); var ok=op?op.id.slice(2):null;
 if(sel){pick(sel,false,ok)}else{draw(); root.querySelector('.mx-ph').textContent=u('tap')}
 if(!keep){try{var url=new URL(location.href); if(lang==='th'){url.searchParams.set('lang','th')}else{url.searchParams.delete('lang')} history.replaceState(null,'',url.toString())}catch(e){}}
}
root.querySelectorAll('.mx-chip').forEach(function(b){b.onclick=function(){f=b.getAttribute('data-f');root.querySelectorAll('.mx-chip').forEach(function(x){x.classList.toggle('on',x===b)}); if(sel&&!counts()[sel]){sel=null;root.querySelector('.mx-ph').textContent=u('tap');root.querySelector('.mx-list').innerHTML=''} draw(); if(sel){pick(sel)}}});
var q=root.querySelector('.mx-q'), res=root.querySelector('.mx-res');
q.addEventListener('input',function(){var t=q.value.toLowerCase().trim(); if(t.length<2){res.style.display='none';return}
 var m=R.filter(function(r){return (r.n+' '+r.club+' '+((r.t&&r.t.n)||'')).toLowerCase().indexOf(t)>-1}).slice(0,12);
 res.innerHTML=m.length?m.map(function(r){return '<button data-k="'+esc(r.k)+'">'+esc(r.n)+(lang==='th'&&r.t&&r.t.n?' ('+esc(r.t.n)+')':'')+' <small>'+esc(r.club)+', '+esc(cn(r.c))+'</small></button>'}).join(''):'<button disabled>'+esc(u('nomatch'))+'</button>';
 res.style.display='block';
 res.querySelectorAll('button[data-k]').forEach(function(b){b.onclick=function(){var r=R.filter(function(x){return x.k===b.getAttribute('data-k')})[0]; res.style.display='none'; q.value=r.n; if(!vis(r)){f='all';root.querySelectorAll('.mx-chip').forEach(function(x){x.classList.toggle('on',x.getAttribute('data-f')==='all')})} pick(r.c,true,r.k)}});
});
document.addEventListener('click',function(e){if(!res.contains(e.target)&&e.target!==q){res.style.display='none'}});
root.querySelectorAll('.mx-lang button').forEach(function(b){b.onclick=function(){setLang(b.getAttribute('data-lang'))}});
setLang(lang,true);
var h=location.hash;
if(h.indexOf('#p-')===0){var r=R.filter(function(x){return 'p-'+x.k===h.slice(1)})[0]; if(r){pick(r.c,true,r.k); root.scrollIntoView()}} else{pick(root.getAttribute('data-start'),false)}
}
if(window.L){start()}else{var t=setInterval(function(){if(window.L){clearInterval(t);start()}},100);setTimeout(function(){clearInterval(t)},15000)} })();
