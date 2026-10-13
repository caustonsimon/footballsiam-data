(function(){var r=document.querySelector('.dx');if(!r)return;
r.querySelectorAll('.dx-tab').forEach(function(b){b.addEventListener('click',function(){r.querySelectorAll('.dx-tab').forEach(function(x){x.classList.toggle('on',x===b)});r.querySelectorAll('.dx-pane').forEach(function(p){p.classList.toggle('on',p.getAttribute('data-p')===b.getAttribute('data-t'))})})});
var f='all',q='';function run(){var g=document.getElementById('dx-half');g.querySelectorAll('.dx-card').forEach(function(c){var ok=(f==='all'||c.getAttribute('data-g')===f)&&(!q||c.getAttribute('data-n').indexOf(q)>-1);c.style.display=ok?'':'none'})}
r.querySelectorAll('.dx-chip').forEach(function(b){b.addEventListener('click',function(){f=b.getAttribute('data-f');r.querySelectorAll('.dx-chip').forEach(function(x){x.classList.toggle('on',x===b)});run()})});
var i=r.querySelector('.dx-q');if(i)i.addEventListener('input',function(){q=i.value.toLowerCase().trim();run()});
if(location.hash&&location.hash.indexOf('#p-')===0){var t=document.getElementById(location.hash.slice(1));if(t)setTimeout(function(){t.scrollIntoView()},300)}})();
