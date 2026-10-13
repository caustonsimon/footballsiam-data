(function(){
function start(){
var root=document.getElementById('mx'); if(!root||!window.L) return;
var D=JSON.parse(new TextDecoder('utf-8').decode(Uint8Array.from(atob(document.getElementById('mx-data').getAttribute('data-d')),function(c){return c.charCodeAt(0)}))), R=D.rows, G=D.geo;
var f='all', sel=null, markers={};
var map=L.map('mx-map',{worldCopyJump:true,scrollWheelZoom:false,zoomSnap:0.5}).setView([38,25],2.5);
var RG={'World':[[-45,-130],[70,150]],'Europe':[[35,-12],[68,32]],'Asia':[[-12,90],[45,145]],'Americas':[[10,-125],[55,-65]]};
var rc=L.control({position:'topright'}); rc.onAdd=function(){var d=L.DomUtil.create('div','mx-rg'); d.innerHTML=Object.keys(RG).map(function(k){return '<button data-r="'+k+'">'+k+'</button>'}).join(''); L.DomEvent.disableClickPropagation(d); d.querySelectorAll('button').forEach(function(b){b.onclick=function(){map.flyToBounds(RG[b.getAttribute('data-r')],{duration:0.6})}}); return d}; rc.addTo(map);
L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}',{attribution:'Tiles &copy; Esri, HERE, Garmin, OpenStreetMap contributors',maxZoom:10}).addTo(map);
function vis(r){return f==='all'||r.g===f}
function esc(s){return String(s||'').replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]})}
function counts(){var c={};R.forEach(function(r){if(vis(r)){c[r.c]=(c[r.c]||0)+1}});return c}
function draw(){
 Object.keys(markers).forEach(function(k){map.removeLayer(markers[k])}); markers={};
 var c=counts();
 Object.keys(c).forEach(function(k){var n=c[k], s=Math.round(22+Math.min(22,Math.sqrt(n)*4));
  var ic=L.divIcon({className:'',html:'<div class="mx-pin'+(k===sel?' sel':'')+'" style="width:'+s+'px;height:'+s+'px;font-size:'+(s>36?14:11)+'px">'+n+'</div>',iconSize:[s,s],iconAnchor:[s/2,s/2]});
  var g=G[k]; var m=L.marker(g,{icon:ic,title:k+': '+n,keyboard:true}).addTo(map);
  m.on('click',function(){pick(k)}); markers[k]=m; });
 var top=root.querySelector('.mx-top'); var arr=Object.keys(c).sort(function(a,b){return c[b]-c[a]});
 top.innerHTML=arr.map(function(k){return '<button data-c="'+esc(k)+'">'+esc(k)+' <b>'+c[k]+'</b></button>'}).join('');
 top.querySelectorAll('button').forEach(function(b){b.onclick=function(){pick(b.getAttribute('data-c'),true)}});
}
function tagc(r){return r.lv==='Thailand international'?'int':(r.lv==='Thailand youth'?'yth':(r.cat==='abroad'?'ab':''))}
function rowHtml(r){
 var dl='<dt>'+(r.cat==='abroad'?'Player':'Heritage')+'</dt><dd>'+esc(r.cat==='abroad'?'Thai':r.her)+'</dd><dt>Thailand</dt><dd>'+esc(r.th)+'</dd>'+(r.oi?'<dt>Also</dt><dd>'+esc(r.oi)+'</dd>':'')+(r.lg?'<dt>League</dt><dd>'+esc(r.lg)+'</dd>':'');
 var s=(r.s||[]).map(function(x){return '<a href="'+esc(x.u)+'" target="_blank" rel="nofollow noopener">'+esc(x.l)+'</a>'}).join(', ');
 return '<div class="mx-row" id="p-'+esc(r.k)+'"><button class="mx-rh" aria-expanded="false"><span><span class="mx-nm">'+esc(r.n)+'</span><span class="mx-cl">'+esc(r.club)+(r.tl?' &middot; '+esc(r.tl):'')+(r.pos?' &middot; '+esc(r.pos):'')+(r.age?' &middot; '+esc(r.age):'')+'</span></span><span class="mx-tag '+tagc(r)+'">'+esc(r.cat==='abroad'&&r.lv==='Professional'?'Thai abroad':r.lv)+'</span></button><div class="mx-det"><dl>'+dl+'</dl><p>'+esc(r.bio)+'</p>'+(r.unc?'<div class="u">Unconfirmed: from a single source</div>':'')+(s?'<div class="s">Sources: '+s+'</div>':'')+'</div></div>';
}
function pick(k,fly,open){
 sel=k; draw();
 if(fly){map.flyTo(G[k],k==='Thailand'||k==='Laos'||k==='Singapore'?5:4,{duration:0.6})}
 var list=R.filter(function(r){return r.c===k&&vis(r)});
 var nd=list.filter(function(r){return r.cat==='dual'}).length, na=list.length-nd;
 root.querySelector('.mx-ph').innerHTML=esc(k)+'<small>'+list.length+' player'+(list.length===1?'':'s')+(nd&&na?' &middot; '+nd+' dual heritage, '+na+' Thai':'')+'</small>';
 var L2=root.querySelector('.mx-list'); L2.innerHTML=list.map(rowHtml).join('');
 L2.querySelectorAll('.mx-rh').forEach(function(b){b.onclick=function(){var row=b.parentNode;var o=row.classList.toggle('open');b.setAttribute('aria-expanded',o?'true':'false')}});
 L2.scrollTop=0;
 if(open){var el=document.getElementById('p-'+open); if(el){el.classList.add('open'); el.querySelector('.mx-rh').setAttribute('aria-expanded','true'); setTimeout(function(){L2.scrollTop=el.offsetTop-L2.offsetTop-6; if(window.innerWidth<900){el.scrollIntoView({block:'center'})}},250)}}
}
root.querySelectorAll('.mx-chip').forEach(function(b){b.onclick=function(){f=b.getAttribute('data-f');root.querySelectorAll('.mx-chip').forEach(function(x){x.classList.toggle('on',x===b)}); if(sel&&!counts()[sel]){sel=null;root.querySelector('.mx-ph').textContent='Tap a country on the map';root.querySelector('.mx-list').innerHTML=''} draw(); if(sel){pick(sel)}}});
var q=root.querySelector('.mx-q'), res=root.querySelector('.mx-res');
q.addEventListener('input',function(){var t=q.value.toLowerCase().trim(); if(t.length<2){res.style.display='none';return}
 var m=R.filter(function(r){return (r.n+' '+r.club).toLowerCase().indexOf(t)>-1}).slice(0,12);
 res.innerHTML=m.length?m.map(function(r){return '<button data-k="'+esc(r.k)+'">'+esc(r.n)+' <small>'+esc(r.club)+', '+esc(r.c)+'</small></button>'}).join(''):'<button disabled>No match</button>';
 res.style.display='block';
 res.querySelectorAll('button[data-k]').forEach(function(b){b.onclick=function(){var r=R.filter(function(x){return x.k===b.getAttribute('data-k')})[0]; res.style.display='none'; q.value=r.n; if(!vis(r)){f='all';root.querySelectorAll('.mx-chip').forEach(function(x){x.classList.toggle('on',x.getAttribute('data-f')==='all')})} pick(r.c,true,r.k)}});
});
document.addEventListener('click',function(e){if(!res.contains(e.target)&&e.target!==q){res.style.display='none'}});
draw();
var h=location.hash; if(h.indexOf('#p-')===0){var r=R.filter(function(x){return 'p-'+x.k===h.slice(1)})[0]; if(r){pick(r.c,true,r.k); root.scrollIntoView()}}
else{pick(root.getAttribute('data-start'),false)}
}
if(window.L){start()}else{var t=setInterval(function(){if(window.L){clearInterval(t);start()}},100);setTimeout(function(){clearInterval(t)},15000)}
})();
