(function(){
'use strict';
var $=function(id){return document.getElementById(id)};
var INDEX=window.CHAT_INDEX||[];
var dirty=false,CHUNK=300, chat=null, msgs=[], start=0, end=0, qterms=null, media=[], term='', hits=[], hitPos=-1, cur=-1;
// ---- config + i18n (config.js is optional: window.VIEWER_CONFIG={lang:'en',title:'Chats'})
var CFG=window.VIEWER_CONFIG||{};
var I18N={
 en:{locale:'en-GB',search:'Search',searchChat:'Search in chat',back:'Back',media:'Media',messages:'Messages',
  pick:'Select a chat',pickHint:'No chats? Run build_viewer.py again after exporting more.',readonly:'Archive – read-only',
  today:'Today',yesterday:'Yesterday',you:'You',youSubj:'You',unavailable:'Media unavailable',audioUnavailable:'Audio unavailable',
  now:'Now',min:'{n} min',hours:'{n}h',noChats:'No chats found. Run build_viewer.py after exporting.',nMsgs:'{n} messages',
  sub:'Archive · {n} messages · {m} photos/videos',loadFail:'Could not load data/{n}.js – rebuild the viewer.',
  deleted:'Deleted message',call:'Call',missed:'Missed call',unknownDate:'Unknown date',older:'Scroll up for older messages ({n})',
  noHits:'0 matches',noMedia:'No photos or videos in this chat.',prevHit:'Previous match',nextHit:'Next match',
  zoom:'Zoom',smaller:'Smaller',larger:'Larger',close:'Close (Esc)',fullscreen:'Fullscreen (F)',prev:'Previous',next:'Next',gsearch:'Search all chats',home:'Home',none:'No matches',hitsN:'{n} matches',inChats:'in {n} chats',from:'From',to:'To',sender:'Sender',chatF:'Chat',all:'All',images:'Photos',video:'Video',audio:'Audio',files:'Files',links:'Links',backRes:'Back to results',newest:'Newest first',oldest:'Oldest first',relevance:'Relevance',searching:'Searching…',newer:'Scroll down for newer messages ({n})',
  sync:'Sync new messages',syncStarting:'Starting sync…',syncRun:'Syncing: {step} · {p} %',syncDone:'+{n} messages, +{m} media',syncDoneChats:'+{n} messages, +{m} media, +{c} chats',syncBusy:'A sync is already running',syncFail:'Sync failed: {msg}',syncNoHelper:'Sync helper not running. Run tools/install_sync.py again.',syncAuth:'Sync helper rejected the token. Run tools/install_sync.py again.'},
 nb:{locale:'nb-NO',search:'Søk',searchChat:'Søk i samtalen',back:'Tilbake',media:'Media',messages:'Meldinger',
  pick:'Velg en samtale',pickHint:'Ingen samtaler? Kjør build_viewer.py på nytt etter flere eksporter.',readonly:'Arkiv – skrivebeskyttet',
  today:'I dag',yesterday:'I går',you:'Deg',youSubj:'Du',unavailable:'Media utilgjengelig',audioUnavailable:'Lyd utilgjengelig',
  now:'Nå',min:'{n} min',hours:'{n}t',noChats:'Ingen samtaler funnet. Kjør build_viewer.py etter eksport.',nMsgs:'{n} meldinger',
  sub:'Arkiv · {n} meldinger · {m} bilder/videoer',loadFail:'Kunne ikke laste data/{n}.js – bygg visningen på nytt.',
  deleted:'Slettet melding',call:'Anrop',missed:'Tapt anrop',unknownDate:'Ukjent dato',older:'Rull opp for eldre meldinger ({n})',
  noHits:'0 treff',noMedia:'Ingen bilder eller videoer i denne samtalen.',prevHit:'Forrige treff',nextHit:'Neste treff',
  zoom:'Zoom',smaller:'Mindre',larger:'Større',close:'Lukk (Esc)',fullscreen:'Fullskjerm (F)',prev:'Forrige',next:'Neste',gsearch:'Søk i alle samtaler',home:'Hjem',none:'Ingen treff',hitsN:'{n} treff',inChats:'i {n} samtaler',from:'Fra',to:'Til',sender:'Avsender',chatF:'Samtale',all:'Alle',images:'Bilder',video:'Video',audio:'Lyd',files:'Filer',links:'Lenker',backRes:'Tilbake til resultater',newest:'Nyeste først',oldest:'Eldste først',relevance:'Relevans',searching:'Søker…',newer:'Rull ned for nyere meldinger ({n})',
  sync:'Synkroniser nye meldinger',syncStarting:'Starter synkronisering…',syncRun:'Synkroniserer: {step} · {p} %',syncDone:'+{n} meldinger, +{m} media',syncDoneChats:'+{n} meldinger, +{m} media, +{c} samtaler',syncBusy:'En synkronisering kjører allerede',syncFail:'Synkronisering feilet: {msg}',syncNoHelper:'Synk-hjelperen kjører ikke. Kjør tools/install_sync.py på nytt.',syncAuth:'Synk-hjelperen avviste nøkkelen. Kjør tools/install_sync.py på nytt.'}
};
var LANG=I18N[CFG.lang]?CFG.lang:'en',L=I18N[LANG];
function T(k,v){var s=L[k]!=null?L[k]:(I18N.en[k]!=null?I18N.en[k]:k);if(v)Object.keys(v).forEach(function(x){s=s.split('{'+x+'}').join(v[x])});return s}
function applyI18n(){
  document.documentElement.lang=LANG;
  var title=CFG.title||'Chats';document.title=title;$('apptitle').textContent=title;$('meav').textContent=title.charAt(0).toUpperCase();
  [].forEach.call(document.querySelectorAll('[data-i18n]'),function(e){e.textContent=T(e.dataset.i18n)});
  [].forEach.call(document.querySelectorAll('[data-i18n-title]'),function(e){e.title=T(e.dataset.i18nTitle);e.setAttribute('aria-label',e.title)});
  [].forEach.call(document.querySelectorAll('[data-i18n-ph]'),function(e){e.placeholder=T(e.dataset.i18nPh)});
}
applyI18n();
function fmt(o){try{return new Intl.DateTimeFormat(L.locale,o)}catch(e){return new Intl.DateTimeFormat('en-GB',o)}}
var F_WD=fmt({weekday:'long'}),F_WS=fmt({weekday:'short'}),F_DM=fmt({day:'numeric',month:'long'}),F_DMY=fmt({day:'numeric',month:'long',year:'numeric'}),
 F_SD=fmt({day:'numeric',month:'short'}),F_SDY=fmt({day:'numeric',month:'short',year:'numeric'});
function cap(s){return s.charAt(0).toUpperCase()+s.slice(1)}
// avatar palette lives in CSS (.av0-.av8, --avN-name)
var NPAL=9;
function hsh(s){var h=0;for(var i=0;i<s.length;i++)h=(h*31+s.charCodeAt(i))|0;return Math.abs(h)%NPAL}
function color(s){return 'var(--av'+hsh(s)+'-name)'}
var SVGNS='http://www.w3.org/2000/svg';
function icon(id,cls){var v=document.createElementNS(SVGNS,'svg'),u=document.createElementNS(SVGNS,'use');u.setAttribute('href','#'+id);v.appendChild(u);if(cls)v.setAttribute('class',cls);return v}
function avatar(e,name,group){e.textContent='';e.className=e.className.replace(/\s*\bav\d\b/g,'')+' av'+hsh(name);
  if(group)e.appendChild(icon('i-group'));else e.textContent=initials(name)}
function initials(n){var p=n.replace(/[^\wÀ-￿ ]/g,'').trim().split(/\s+/);return ((p[0]||'?')[0]+(p.length>1?p[p.length-1][0]:'')).toUpperCase()}
function el(t,c,txt){var e=document.createElement(t);if(c)e.className=c;if(txt!=null)e.textContent=txt;return e}
function pad(n){return (n<10?'0':'')+n}
function day(t){var d=new Date(t);return d.getFullYear()+'-'+pad(d.getMonth()+1)+'-'+pad(d.getDate())}
function hm(t){var d=new Date(t);return pad(d.getHours())+':'+pad(d.getMinutes())}
function yday(){return day(Date.now()-864e5)}
function dlabel(t){var d=new Date(t),n=new Date();if(day(t)===day(+n))return T('today');if(day(t)===yday())return T('yesterday');
  return cap(F_WD.format(d))+' '+(d.getFullYear()!==n.getFullYear()?F_DMY:F_DM).format(d)}
function short(t){if(!t)return '';var n=new Date(),d=new Date(t),df=n-d;
  if(df>=0&&df<6e4)return T('now');if(df>=0&&df<36e5)return T('min',{n:Math.floor(df/6e4)});
  if(df>=0&&df<864e5)return T('hours',{n:Math.floor(df/36e5)});
  if(df<6048e5)return F_WS.format(d);
  return (d.getFullYear()!==n.getFullYear()?F_SDY:F_SD).format(d)}
function full(t){return F_DMY.format(new Date(t))+' '+hm(t)}
function szs(b){if(!b)return '';return b>1048576?(b/1048576).toFixed(1)+' MB':Math.max(1,Math.round(b/1024))+' KB'}

// ---- sidebar
function renderList(){
  var f=$('chatfilter').value.toLowerCase(), box=$('chatlist');box.textContent='';
  var l=INDEX.slice().sort(function(a,b){return b.last-a.last});
  if(!l.length){box.appendChild(el('div','loadmore',T('noChats')))}
  l.forEach(function(c){
    if(f&&c.name.toLowerCase().indexOf(f)<0)return;
    var d=el('div','ci'+(chat&&chat.id===c.id?' sel':''));d.dataset.id=c.id;
    var av=el('div','avatar');avatar(av,c.name,c.group);
    var t=el('div','t'),r=el('div','r1');r.appendChild(el('span','n',c.name));r.appendChild(el('span','tm',short(c.last)));
    var r2=el('div','r2'),sn=el('div','sn'),pre=c.lo?T('you'):(c.group&&c.ls?c.ls:'');
    if(pre)sn.appendChild(el('b',null,pre+': '));sn.appendChild(document.createTextNode(c.snippet||T('nMsgs',{n:c.count})));
    r2.appendChild(sn);if(c.lo)r2.appendChild(icon('i-read','rk'));t.appendChild(r);t.appendChild(r2);
    d.appendChild(av);d.appendChild(t);d.onclick=function(){openChat(c.id)};box.appendChild(d)});
}
$('chatfilter').oninput=renderList;

// ---- open chat
function openChat(id,target){
  var go=function(){
    chat=window.CHATS[id];msgs=chat.messages;media=[];
    msgs.forEach(function(m,i){m.a.forEach(function(a,j){if(a.type==='image'||a.type==='video')media.push({mi:i,a:a})})});
    $('empty').hidden=true;$('chat').hidden=false;document.body.classList.add('inchat');
    $('htitle').textContent=chat.name;avatar($('hav'),chat.name,chat.group);
    $('hsub').textContent=T('sub',{n:msgs.length,m:media.length});
    $('search').value='';term='';hits=[];$('scount').textContent='';
    qterms=null;$('backres').hidden=!(target!=null&&GS.open);
    showTab('msg');
    if(target!=null){start=Math.max(0,target-150);end=Math.min(msgs.length,target+150);qterms=GS.q?GS.q.hl:null;renderMsgs(false);flash(target)}
    else{start=Math.max(0,msgs.length-CHUNK);end=msgs.length;renderMsgs(true)}
    renderList();
  };
  if(window.CHATS[id])return go();
  var s=document.createElement('script');s.src='data/'+id+'.js';s.onload=go;
  s.onerror=function(){alert(T('loadFail',{n:id}))};document.head.appendChild(s);
}
$('back').onclick=function(){document.body.classList.remove('inchat')};
function flash(i){var e=$('m'+i);if(!e)return;e.scrollIntoView({block:'center'});e.classList.remove('flash');void e.offsetWidth;e.classList.add('flash')}
function showTab(t){
  $('msgs').hidden=t!=='msg';$('mediagrid').hidden=t!=='media';$('mzoom').hidden=t!=='media';
  $('tabmsg').classList.toggle('on',t==='msg');$('tabmedia').classList.toggle('on',t==='media');
  if(t==='media')renderMediaGrid();
}
function tools(on,search){var c=$('chat');c.classList.toggle('toolsopen',on);$('tools').hidden=!(on&&search);if(on&&search)$('search').focus()}
$('csearch').onclick=function(){tools($('tools').hidden,true)};
$('cmore').onclick=function(){tools(!$('chat').classList.contains('toolsopen'),false)};
$('tabmsg').onclick=function(){showTab('msg')};$('tabmedia').onclick=function(){showTab('media')};

// ---- lazy media
var io=('IntersectionObserver' in window)?new IntersectionObserver(function(es){es.forEach(function(e){
  if(e.isIntersecting){var v=e.target;if(v.dataset.src){v.src=v.dataset.src;delete v.dataset.src}io.unobserve(v)}})},{rootMargin:'300px'}):null;
function placeholder(th,name){th.textContent='';th.appendChild(el('div','ph',T('unavailable')+(name?'\n'+name:'')));th.style.cursor='default';th.onclick=null}
function thumb(a,idx,over){
  var th=el('div','th');
  if(!a.ok){placeholder(th,a.name);return th}
  var m;
  if(a.type==='video'){
    m=document.createElement('video');m.preload='metadata';m.muted=true;m.playsInline=true;
    m.dataset.src=a.path+'#t=0.1';if(io)io.observe(m);else m.src=a.path+'#t=0.1';
    th.appendChild(m);th.appendChild(el('div','play','▶'));
  }else{m=document.createElement('img');m.loading='lazy';m.decoding='async';m.src=a.path;m.alt=''}
  m.onerror=function(){placeholder(th,a.name)};
  // single media: once real dimensions are known, size the box to the media's own aspect ratio (no grey filler)
  var fit=function(){var w=m.naturalWidth||m.videoWidth,h=m.naturalHeight||m.videoHeight;if(w&&h){th.style.aspectRatio=w+'/'+h;th.classList.add('ld')}};
  if(a.type==='video')m.addEventListener('loadedmetadata',fit);else{m.addEventListener('load',fit);if(m.complete)fit()}
  if(a.type!=='video')th.appendChild(m);
  if(over)th.appendChild(el('div','more','+'+over));
  th.onclick=function(){openLB(idx)};
  return th;
}
var mediaIdx=new Map();
function mIndex(a){var i=media.findIndex(function(x){return x.a===a});return i}

// ---- text
var URLRE=/(https?:\/\/[^\s<>"']+)/g;
function addText(parent,text){
  var parts=text.split(URLRE);
  parts.forEach(function(p,i){
    if(i%2){var a=el('a',null,p);a.href=p;a.target='_blank';a.rel='noopener noreferrer';parent.appendChild(a)}
    else hl(parent,p)});
}
function hl(parent,p){
  if(!qterms||!qterms.length||!p){parent.appendChild(document.createTextNode(p));return}
  hlInto(parent,p,qterms);
}

// ---- messages
function buildMsg(i,prev){
  var m=msgs[i],f=document.createDocumentFragment();
  if(!prev||day(prev.t)!==day(m.t)){var s=el('div','sep');s.appendChild(el('span',null,m.t?dlabel(m.t):T('unknownDate')));f.appendChild(s)}
  if(m.k){var KN={'deleted':T('deleted'),'call':T('call'),'missed call':T('missed')};f.appendChild(el('div','sys'+(m.k!=='deleted'?' callev':''),(m.o?T('youSubj'):m.s)+' · '+(KN[m.k]||m.k)+' · '+hm(m.t)));f.lastChild.id='m'+i;return f}
  var first=!prev||prev.s!==m.s||day(prev.t)!==day(m.t)||prev.k;
  var nx=msgs[i+1],grpNext=nx&&!nx.k&&nx.s===m.s&&day(nx.t)===day(m.t)&&!(m.r&&m.r.length);
  var row=el('div','row '+(m.o?'out':'in')+(first?' first':' gp')+(grpNext?' gn':''));row.id='m'+i;
  var col=el('div','col');
  if(first&&!m.o&&chat.group){var w=el('div','who',m.s);w.style.color=color(m.s);col.appendChild(w)}
  var vis=m.a.filter(function(a){return a.type==='image'||a.type==='video'});
  var other=m.a.filter(function(a){return a.type!=='image'&&a.type!=='video'});
  var only=!m.b&&!m.q&&!other.length&&vis.length;
  var b=el('div','bub'+(only?' media-only':(vis.length?' has-media':'')));
  if(m.q){b.appendChild(el('div','quote',m.q))}
  if(vis.length){
    var n=Math.min(vis.length,4),g=el('div','grid g'+n);
    for(var j=0;j<n;j++)g.appendChild(thumb(vis[j],mIndex(vis[j]),(j===3&&vis.length>4)?vis.length-4:0));
    b.appendChild(g)}
  other.forEach(function(a){
    if(a.type==='audio'&&a.ok){var d=el('div','audio'),au=document.createElement('audio');au.controls=true;au.preload='none';au.src=a.path;au.onerror=function(){d.textContent=T('audioUnavailable')};d.appendChild(au);b.appendChild(d)}
    else{var c=el(a.ok?'a':'div','file');if(a.ok){c.href=a.path;c.target='_blank'}c.appendChild(el('span',null,a.ok?'📄':'⚠'));
      var t=el('div');t.appendChild(el('b',null,a.name||'file'));t.appendChild(el('small',null,a.ok?szs(a.size):T('unavailable')));c.appendChild(t);b.appendChild(c)}});
  var mt=el('span','meta',hm(m.t));if(m.o)mt.appendChild(icon('i-read'));
  if(m.b){var tx=el('div','m');addText(tx,m.b);tx.appendChild(mt);b.appendChild(tx)}else b.appendChild(mt);
  col.appendChild(b);
  if(m.r&&m.r.length){var r=el('div','reacts'),G=rgroup(m.r);r.dataset.i=i;r.setAttribute('role','group');
    r.title=G.map(rline).join('\n');r.setAttribute('aria-label',G.map(rline).join('; '));
    G.forEach(function(g){var p=el('span','pill',g.e+(g.n.length>1?' '+g.n.length:''));p.tabIndex=0;p.setAttribute('role','button');p.setAttribute('aria-label',rline(g));r.appendChild(p)});col.appendChild(r)}
  row.appendChild(col);f.appendChild(row);return f;
}
// ---- reaction popover: who reacted with what (hover on desktop, tap on touch)
function rname(n){return n==='Me'?T('you'):(n||'?')}
function rgroup(rs){var G=[],ix={};rs.forEach(function(x){var e=x[1];if(ix[e]==null){ix[e]=G.length;G.push({e:e,n:[]})}G[ix[e]].n.push(rname(x[0]))});return G}
function rline(g){return g.e+'  '+g.n.join(', ')}
var RP={el:null,anchor:null,pin:false};
function rpShow(anchor,pin){var r=anchor.closest('.reacts');if(!r)return;var m=msgs[+r.dataset.i];if(!m||!m.r)return;
  var p=RP.el;if(!p){p=RP.el=el('div');p.id='rpop';p.setAttribute('role','tooltip');document.body.appendChild(p)}
  p.textContent='';rgroup(m.r).forEach(function(g){var ln=el('div','rpl');ln.appendChild(el('span','rpe',g.e));ln.appendChild(el('span','rpn',g.n.join(', ')));p.appendChild(ln)});
  p.hidden=false;RP.anchor=r;RP.pin=!!pin;r.setAttribute('aria-describedby','rpop');
  if(r.title){r.dataset.t=r.title;r.removeAttribute('title')}
  rpPlace()}
function rpPlace(){var p=RP.el,r=RP.anchor;if(!p||!r)return;if(!r.isConnected){rpHide();return}
  var a=r.getBoundingClientRect(),W=innerWidth,H=innerHeight,w=p.offsetWidth,h=p.offsetHeight,M=8;
  var top=a.top-h-6;if(top<M)top=a.bottom+6;if(top+h>H-M)top=Math.max(M,H-M-h);
  var left=Math.min(Math.max(M,a.left),W-M-w);if(left<M)left=M;
  p.style.top=top+'px';p.style.left=left+'px'}
function rpHide(){RP.force=false;if(RP.el)RP.el.hidden=true;var r=RP.anchor;if(r){r.removeAttribute('aria-describedby');if(r.dataset.t){r.title=r.dataset.t;delete r.dataset.t}}RP.anchor=null;RP.pin=false}
var HOVER=window.matchMedia&&matchMedia('(hover:hover)').matches;
$('msgs').addEventListener('mouseover',function(e){if(!HOVER||RP.pin)return;var r=e.target.closest&&e.target.closest('.reacts');if(r&&r!==RP.anchor)rpShow(r)});
$('msgs').addEventListener('mouseout',function(e){if(!HOVER||RP.pin||!RP.anchor)return;if(!e.relatedTarget||!RP.anchor.contains(e.relatedTarget))rpHide()});
$('msgs').addEventListener('focusin',function(e){if(e.target.classList&&e.target.classList.contains('pill'))rpShow(e.target)});
$('msgs').addEventListener('focusout',function(){if(!RP.pin)rpHide()});
document.addEventListener('click',function(e){var pl=e.target.closest&&e.target.closest('.reacts .pill');
  if(pl){var r=pl.closest('.reacts');if(RP.pin&&RP.anchor===r)rpHide();else rpShow(pl,true);return}
  if(RP.el&&!RP.el.contains(e.target))rpHide()});
document.addEventListener('keydown',function(e){if(e.key==='Escape'&&RP.anchor)rpHide();
  else if((e.key==='Enter'||e.key===' ')&&e.target.classList&&e.target.classList.contains('pill')&&e.target.closest('.reacts')){e.preventDefault();rpShow(e.target,true)}});
$('msgs').addEventListener('scroll',function(){if(!RP.anchor)return;if(RP.pin||RP.force)rpPlace();else rpHide()},{passive:true});
window.addEventListener('resize',function(){if(RP.anchor)rpPlace()});
function renderMsgs(bottom){
  rpHide();var box=$('msgs');box.textContent='';
  if(start>0){var lm=el('div','loadmore',T('older',{n:start}));lm.id='lm';box.appendChild(lm)}
  var fr=document.createDocumentFragment();
  if(!end||end>msgs.length)end=msgs.length;
  for(var i=start;i<end;i++)fr.appendChild(buildMsg(i,i>0?msgs[i-1]:null));
  box.appendChild(fr);
  if(end<msgs.length){var ln=el('div','loadmore',T('newer',{n:msgs.length-end}));ln.id='ln';box.appendChild(ln)}
  if(bottom){box.scrollTop=box.scrollHeight;stick=Date.now()+4000}
}
var stick=0;$('msgs').addEventListener('load',function(){if(Date.now()<stick){var b=$('msgs');b.scrollTop=b.scrollHeight}},true);
['wheel','touchstart','keydown'].forEach(function(ev){$('msgs').addEventListener(ev,function(){stick=0},{passive:true})});
$('msgs').addEventListener('scroll',function(){
  var box=$('msgs');if(box.scrollTop<60&&start>0)loadOlder();
  else if(end<msgs.length&&box.scrollTop+box.clientHeight>box.scrollHeight-60)loadNewer()});
function loadNewer(){
  var ne=Math.min(msgs.length,end+CHUNK),fr=document.createDocumentFragment();
  for(var i=end;i<ne;i++)fr.appendChild(buildMsg(i,i>0?msgs[i-1]:null));
  var ln=$('ln');end=ne;if(ln){ln.before(fr);if(end>=msgs.length)ln.remove();else ln.textContent=T('newer',{n:msgs.length-end})}
}
function loadOlder(){
  var box=$('msgs'),old=box.scrollHeight,ns=Math.max(0,start-CHUNK);
  var fr=document.createDocumentFragment();
  for(var i=ns;i<start;i++)fr.appendChild(buildMsg(i,i>0?msgs[i-1]:null));
  var first=box.querySelector('.sep,.row,.sys');
  // first rendered message may have had a separator that now duplicates; remove stale leading separator
  if(first&&first.classList.contains('sep')&&start>0&&day(msgs[start-1].t)===day(msgs[start].t))first.remove();
  var lm=$('lm');start=ns;
  if(lm){lm.after(fr);if(start===0)lm.remove();else lm.textContent=T('older',{n:start})}
  box.scrollTop=box.scrollHeight-old+box.scrollTop;
}

// ---- search
function doSearch(){
  var q=parseQ($('search').value);hits=[];hitPos=-1;qterms=q?q.hl:null;
  if(!chat||!q){$('scount').textContent='';if(chat){showTab('msg');renderMsgs(false)}return}
  var cid=chat.id;
  loadShard(cid,function(){if(!chat||chat.id!==cid)return;var sh=window.SEARCH[cid];
    for(var i=0;i<sh.m.length;i++)if(matchRow(sh,sh.m[i],q,null))hits.push(i);
    $('scount').textContent=hits.length?'0/'+hits.length:T('noHits');
    if(hits.length){hitPos=hits.length-1;dirty=true;jump()}else{showTab('msg');renderMsgs(false)}})
}
function jump(){
  if(!hits.length)return;showTab('msg');
  var i=hits[hitPos];
  if(dirty||!(i>=start&&i<end&&$('m'+i))){dirty=false;if(!(i>=start&&i<end)){start=Math.max(0,i-150);end=Math.min(msgs.length,i+150)}renderMsgs(false)}
  else{[].forEach.call($('msgs').querySelectorAll('mark.cur'),function(x){x.classList.remove('cur')});
}
  var e=$('m'+i);if(e){e.scrollIntoView({block:'center'});
    var ms=e.querySelectorAll('mark.hit');if(ms[0])ms[0].classList.add('cur')}
  $('scount').textContent=(hitPos+1)+'/'+hits.length;
}
var st;$('search').oninput=function(){clearTimeout(st);st=setTimeout(doSearch,150)};
$('search').onkeydown=function(e){if(e.key==='Enter'){e.preventDefault();step(e.shiftKey?1:-1)}};
function step(d){if(!hits.length)return;hitPos=(hitPos+d+hits.length)%hits.length;jump()}
$('sprev').onclick=function(){step(-1)};$('snext').onclick=function(){step(1)};

// ---- media grid
function renderMediaGrid(){
  var g=$('mediagrid');g.textContent='';
  if(!media.length){g.appendChild(el('div','loadmore',T('noMedia')));return}
  var fr=document.createDocumentFragment();
  for(var i=media.length-1;i>=0;i--)fr.appendChild(thumb(media[i].a,i,0));
  g.appendChild(fr);
}

// ---- media zoom (slider, -/+, ctrl/cmd+wheel, pinch); size remembered per viewer
var ZMIN=60,ZMAX=320,zsize=window.innerWidth<600?100:140;
try{var zs=+localStorage.getItem('sb.thumb');if(zs>=ZMIN&&zs<=ZMAX)zsize=zs}catch(e){}
function setZoom(v,save){zsize=Math.max(ZMIN,Math.min(ZMAX,Math.round(v)));$('chat').style.setProperty('--thumb',zsize+'px');$('zrange').value=zsize;
  if(save!==false)try{localStorage.setItem('sb.thumb',zsize)}catch(e){}}
setZoom(zsize,false);
$('zrange').oninput=function(){setZoom(+this.value)};
$('zout').onclick=function(){setZoom(zsize/1.25)};$('zin').onclick=function(){setZoom(zsize*1.25)};
$('mediagrid').addEventListener('wheel',function(e){if(!(e.ctrlKey||e.metaKey))return;e.preventDefault();setZoom(zsize*(e.deltaY<0?1.1:1/1.1))},{passive:false});
var pz=null;function pd(t){return Math.hypot(t[0].clientX-t[1].clientX,t[0].clientY-t[1].clientY)}
$('mediagrid').addEventListener('touchstart',function(e){if(e.touches.length===2)pz={d:pd(e.touches),z:zsize}},{passive:true});
$('mediagrid').addEventListener('touchmove',function(e){if(pz&&e.touches.length===2){e.preventDefault();setZoom(pz.z*pd(e.touches)/pz.d,false)}},{passive:false});
$('mediagrid').addEventListener('touchend',function(e){if(pz&&e.touches.length<2){setZoom(zsize);pz=null}},{passive:true});
var gs0=0;$('mediagrid').addEventListener('gesturestart',function(e){e.preventDefault();gs0=zsize});
$('mediagrid').addEventListener('gesturechange',function(e){e.preventDefault();setZoom(gs0*e.scale)});

// ---- lightbox
function openLB(i){if(i<0)return;rpHide();cur=i;$('lb').hidden=false;showLB()}
function closeLB(){$('lb').hidden=true;$('lbstage').textContent='';cur=-1;
  if(document.fullscreenElement)document.exitFullscreen()}
function showLB(){
  var it=media[cur],a=it.a,st=$('lbstage');st.textContent='';st.classList.remove('zoom');
  var m;
  if(!a.ok){st.appendChild(el('div','ph',T('unavailable')));}
  else if(a.type==='video'){m=document.createElement('video');m.controls=true;m.autoplay=true;m.src=a.path;m.onerror=function(){st.textContent='';st.appendChild(el('div','ph',T('unavailable')))}}
  else{m=document.createElement('img');m.src=a.path;m.onerror=function(){st.textContent='';st.appendChild(el('div','ph',T('unavailable')))};
    m.onclick=function(){st.classList.toggle('zoom')};m.ondblclick=function(){st.classList.toggle('zoom')}}
  if(m)st.appendChild(m);
  var msg=msgs[it.mi];
  var cp=$('lbcap'),cs=cp.children;cs[0].textContent=msg.o?T('you'):(msg.s||chat.name);cs[1].textContent=msg.t?full(msg.t):'';
  var lr=cs[2];if(!lr){lr=el('span','lbr');cp.appendChild(lr)}lr.textContent=msg.r&&msg.r.length?rgroup(msg.r).map(rline).join('  ·  '):'';lr.hidden=!lr.textContent;
  $('lbcount').textContent=(cur+1)+' / '+media.length;
  [cur-1,cur+1].forEach(function(k){if(media[k]&&media[k].a.type==='image'&&media[k].a.ok)new Image().src=media[k].a.path});
}
function nav(d){if(!media.length)return;cur=(cur+d+media.length)%media.length;showLB()}
$('lbclose').onclick=closeLB;
var tx0=null,ty0=0;$('lb').addEventListener('touchstart',function(e){if(e.touches.length===1&&!$('lbstage').classList.contains('zoom')){tx0=e.touches[0].clientX;ty0=e.touches[0].clientY}else tx0=null},{passive:true});
$('lb').addEventListener('touchend',function(e){if(tx0==null)return;var t=e.changedTouches[0],dx=t.clientX-tx0,dy=t.clientY-ty0;tx0=null;
  if(Math.abs(dx)>50&&Math.abs(dx)>Math.abs(dy)*1.5)nav(dx<0?1:-1);else if(dy>120&&Math.abs(dy)>Math.abs(dx)*2)closeLB()},{passive:true});$('lbbg').onclick=closeLB;$('lbstage').onclick=function(e){if(e.target===this)closeLB()};
$('lbprev').onclick=function(){nav(-1)};$('lbnext').onclick=function(){nav(1)};
$('lbfs').onclick=function(){if(document.fullscreenElement)document.exitFullscreen();else $('lb').requestFullscreen&&$('lb').requestFullscreen()};
document.addEventListener('keydown',function(e){
  if($('lb').hidden)return;
  if(e.key==='Escape')closeLB();else if(e.key==='ArrowLeft')nav(-1);else if(e.key==='ArrowRight')nav(1);
  else if(e.key==='f'||e.key==='F')$('lbfs').onclick();});

renderList();

// ---- search engine (shared by global + in-chat search). Shards: search/<n>.js -> window.SEARCH[n]={s:[senders],m:[[t,si,text,norm|0,flags]]}
window.SEARCH=window.SEARCH||{};
var FOLD={'æ':'ae','ø':'o','å':'a','ß':'ss','œ':'oe','đ':'d','ł':'l','þ':'th'};
function normc(c){c=c.toLowerCase();if(FOLD[c])return FOLD[c];return c.normalize?c.normalize('NFKD').replace(/[̀-ͯ]/g,''):c}
function norm(s){var o='';for(var i=0;i<s.length;i++)o+=normc(s[i]);return o}
function parseQ(raw){
  raw=(raw||'').trim();if(!raw)return null;
  var inc=[],exc=[],re=/(-?)"([^"]*)"?|(-?)(\S+)/g,m;
  while((m=re.exec(raw))){var neg=m[1]||m[3],t=norm(m[2]!=null?m[2]:m[4]).trim();if(!t||t==='-')continue;(neg?exc:inc).push(t)}
  if(!inc.length&&!exc.length)return null;
  return {inc:inc,exc:exc,hl:inc.slice().sort(function(a,b){return b.length-a.length})};
}
var shardWait={};
function loadShard(id,cb){
  if(window.SEARCH[id])return cb();
  if(shardWait[id])return shardWait[id].push(cb);shardWait[id]=[cb];
  var s=document.createElement('script');s.src='search/'+id+'.js';
  s.onload=s.onerror=function(){if(!window.SEARCH[id])window.SEARCH[id]={s:[],m:[]};var l=shardWait[id];delete shardWait[id];l.forEach(function(f){f()})};
  document.head.appendChild(s);
}
// f: {sender, from, to (sec), type bit}
function matchRow(sh,r,q,f){
  if(f){if(f.from&&r[0]<f.from)return false;if(f.to&&r[0]>f.to)return false;if(f.type&&!(r[4]&f.type))return false;
    if(f.sender&&norm(sh.s[r[1]]).indexOf(f.sender)<0)return false}
  var t=r[3]||r[2];
  for(var i=0;i<q.inc.length;i++)if(t.indexOf(q.inc[i])<0)return false;
  for(i=0;i<q.exc.length;i++)if(t.indexOf(q.exc[i])>=0)return false;
  return true;
}
// highlight terms in original text using a char->normalized offset map
function hlInto(parent,p,terms){
  var n='',map=[];for(var i=0;i<p.length;i++){var c=normc(p[i]);for(var j=0;j<c.length;j++)map.push(i);n+=c}
  var mk=new Uint8Array(p.length);
  terms.forEach(function(t){if(!t)return;var k=0;while((k=n.indexOf(t,k))>=0){for(var x=map[k];x<=map[k+t.length-1];x++)mk[x]=1;k+=t.length}});
  var i0=0;
  while(i0<p.length){var v=mk[i0],j=i0;while(j<p.length&&mk[j]===v)j++;
    parent.appendChild(v?el('mark','hit',p.slice(i0,j)):document.createTextNode(p.slice(i0,j)));i0=j}
}
function snippet(p,terms){
  var n=norm(p),k=-1;for(var i=0;i<terms.length&&k<0;i++)k=n.indexOf(terms[i]);
  if(k<0||p.length<=120)return p.length>140?p.slice(0,140)+'…':p;
  var a=Math.max(0,Math.round(k*p.length/Math.max(1,n.length))-40);return (a?'…':'')+p.slice(a,a+140)+(a+140<p.length?'…':'');
}
// ---- global search UI
var GS={open:false,q:null,res:[],shown:0,gen:0};
function gsOpen(on){GS.open=on;$('gsearch').hidden=!on;$('chatlist').hidden=on;$('lfilter').hidden=true;
  if(on){$('gq').focus();fillChats()}}
function fillChats(){var sel=$('gchat');if(sel.options.length>1)return;
  INDEX.slice().sort(function(a,b){return a.name.localeCompare(b.name)}).forEach(function(c){var o=el('option',null,c.name);o.value=c.id;sel.appendChild(o)})}
$('lsearch').onclick=function(){gsOpen(!GS.open)};
$('backres').onclick=function(){document.body.classList.remove('inchat');gsOpen(true);$('backres').hidden=false};
function dsec(v,endOfDay){if(!v)return 0;var d=new Date(v+'T00:00:00');return Math.floor(d.getTime()/1000)+(endOfDay?86399:0)}
var gst;['gq','gsender'].forEach(function(id){$(id).oninput=function(){clearTimeout(gst);gst=setTimeout(runGlobal,150)}});
['gchat','gfrom','gto','gtype','gsort'].forEach(function(id){$(id).onchange=runGlobal});
function runGlobal(){
  var gen=++GS.gen,q=parseQ($('gq').value);GS.q=q;GS.res=[];GS.shown=0;
  var box=$('gres');box.textContent='';$('gcount').textContent='';
  if(!q)return;
  var f={sender:norm($('gsender').value.trim()),from:dsec($('gfrom').value),to:dsec($('gto').value,1),type:+$('gtype').value};
  var ids=$('gchat').value!==''?[+$('gchat').value]:INDEX.map(function(c){return c.id});
  $('gcount').textContent=T('searching');var t0=performance.now(),ci=0,ri=0;
  function step(){
    if(gen!==GS.gen)return;var budget=performance.now()+12;
    while(ci<ids.length){
      var id=ids[ci],sh=window.SEARCH[id];
      if(!sh){return loadShard(id,function(){setTimeout(step,0)})}
      for(;ri<sh.m.length;ri++){var r=sh.m[ri];if(matchRow(sh,r,q,f))GS.res.push({c:id,i:ri,r:r,sh:sh});
        if((ri&1023)===1023&&performance.now()>budget){ri++;return setTimeout(step,0)}}
      ci++;ri=0;
    }
    GS.ms=Math.round(performance.now()-t0);window.__lastSearchMs=GS.ms;finish();
  }
  step();
}
function score(h){var t=h.r[3]||h.r[2],s=0;GS.q.inc.forEach(function(w){var k=t.indexOf(w);s+=10;if(k===0||/\W/.test(t[k-1]||' '))s+=5;
  var e=t[k+w.length];if(!e||/\W/.test(e))s+=5});return s-Math.min(5,t.length/200)}
function finish(){
  var mode=$('gsort').value,R=GS.res;
  if(mode==='old')R.sort(function(a,b){return a.r[0]-b.r[0]});
  else if(mode==='rel'){R.forEach(function(h){h.sc=score(h)});R.sort(function(a,b){return b.sc-a.sc||b.r[0]-a.r[0]})}
  else R.sort(function(a,b){return b.r[0]-a.r[0]});
  // group by chat; chat order = first occurrence in sorted list
  var g={},order=[];R.forEach(function(h){if(!g[h.c]){g[h.c]=[];order.push(h.c)}g[h.c].push(h)});
  GS.flat=[];order.forEach(function(c){g[c].forEach(function(h,k){h.head=k===0?g[c].length:0;GS.flat.push(h)})});
  $('gcount').textContent=R.length?T('hitsN',{n:R.length})+' '+T('inChats',{n:order.length})+' · '+GS.ms+' ms':T('none');
  GS.shown=0;$('gres').textContent='';more();
}
var NAMES={};INDEX.forEach(function(c){NAMES[c.id]=c});
function more(){
  var box=$('gres'),fr=document.createDocumentFragment(),endi=Math.min(GS.flat.length,GS.shown+200);
  for(var k=GS.shown;k<endi;k++){var h=GS.flat[k];
    if(h.head){var c=NAMES[h.c]||{name:'?'},hd=el('div','ghead');var av=el('div','avatar sm');avatar(av,c.name,c.group);hd.appendChild(av);
      hd.appendChild(el('span','n',c.name));hd.appendChild(el('span','cnt',String(h.head)));fr.appendChild(hd)}
    var it=el('div','ghit'),top=el('div','gtop'),snd=h.sh.s[h.r[1]];
    top.appendChild(el('b',null,snd==='Me'?T('you'):snd));top.appendChild(el('span','tm',h.r[0]?F_SDY.format(new Date(h.r[0]*1000))+' '+hm(h.r[0]*1000):''));
    it.appendChild(top);var sn=el('div','gsn');hlInto(sn,snippet(h.r[2],GS.q.hl),GS.q.hl);it.appendChild(sn);
    (function(h){it.onclick=function(){openChat(h.c,h.i)}})(h);fr.appendChild(it)}
  GS.shown=endi;box.appendChild(fr);
}
$('gres').addEventListener('scroll',function(){var b=this;if(GS.flat&&GS.shown<GS.flat.length&&b.scrollTop+b.clientHeight>b.scrollHeight-400)more()});
window.__gsearch=function(v){gsOpen(true);$('gq').value=v;runGlobal()};
var q=location.hash.match(/chat=(\d+)/),ql=location.hash.match(/lb=(\d+)/);if(q)openChat(+q[1]);
if(q&&ql)(function w(){if(chat)openLB(+ql[1]);else setTimeout(w,50)})();
var qt=location.hash.match(/tab=media/),qz=location.hash.match(/z=(\d+)/);if(qz)setZoom(+qz[1],false);
if(q&&qt)(function w(){if(chat){tools(true,false);showTab('media')}else setTimeout(w,50)})();
var HP={};location.hash.slice(1).split('&').forEach(function(kv){var k=kv.split('=');HP[k[0]]=decodeURIComponent(k[1]||'')});
if(HP.gs!=null){gsOpen(true);$('gq').value=HP.gs;if(HP.gtype)$('gtype').value=HP.gtype;if(HP.gfrom)$('gfrom').value=HP.gfrom;if(HP.gsort)$('gsort').value=HP.gsort;runGlobal();
  if(HP.hit)(function w(){if(GS.flat&&GS.flat.length){var h=GS.flat[+HP.hit];openChat(h.c,h.i)}else setTimeout(w,50)})()}
if(q&&HP.rp!=null)(function w(){var r=chat&&document.querySelector('#msgs .reacts[data-i="'+(HP.rp==='last'?msgs.length-1:+HP.rp)+'"]');if(r){r.scrollIntoView({block:'center'});setTimeout(function(){RP.force=true;rpShow(r.querySelector('.pill')||r,true)},150)}else setTimeout(w,50)})();
if(q&&HP.cs)(function w(){if(chat){window.__search(HP.cs);if(HP.cn)setTimeout(function(){for(var k=0;k<+HP.cn;k++)step(-1)},300)}else setTimeout(w,50)})();
// ---- Home reset: title/avatar click or Esc with nothing open
function home(){
  rpHide();if(!$('lb').hidden)closeLB();
  GS.gen++;GS.q=null;GS.res=[];GS.flat=[];GS.shown=0;
  ['gq','gsender','gfrom','gto'].forEach(function(id){$(id).value=''});$('gchat').value='';$('gtype').value='0';$('gsort').value='new';
  $('gres').textContent='';$('gcount').textContent='';gsOpen(false);
  $('chatfilter').value='';$('lfilter').hidden=true;
  $('search').value='';term='';hits=[];hitPos=-1;qterms=null;$('scount').textContent='';tools(false,false);
  $('backres').hidden=true;
  if(chat){showTab('msg');start=Math.max(0,msgs.length-CHUNK);end=msgs.length;renderMsgs(true)}
  renderList();
  document.body.classList.remove('inchat');
  if(location.hash)try{history.replaceState(null,'',location.pathname+location.search)}catch(e){}
}
['apptitle','meav'].forEach(function(id){var e=$(id);e.classList.add('homebtn');e.tabIndex=0;e.setAttribute('role','button');
  e.setAttribute('aria-label',T('home'));e.title=T('home');
  e.addEventListener('click',home);e.addEventListener('keydown',function(ev){if(ev.key==='Enter'||ev.key===' '){ev.preventDefault();home()}})});
window.addEventListener('keydown',function(e){if(e.key!=='Escape')return;
  if(RP.anchor||!$('lb').hidden)return; // handled by popover / lightbox handlers
  if(GS.open){gsOpen(false);return}
  if(!$('tools').hidden||$('chat').classList.contains('toolsopen')){tools(false,false);return}
  home()},true);
window.__home=home;
window.__openChat=openChat;window.__search=function(v){tools(true,true);$('search').value=v;doSearch()};window.__openLB=openLB;

// ---- re-sync (optional): sync_config.js (local only, written by tools/install_sync.py) holds
// {port, token} for the 127.0.0.1 helper (tools/sync_helper.py). No config -> no button.
// The helper only starts pipeline/sync.py; new data appears via the build_stamp auto-refresh below.
(function(){
  var SC=window.SYNC_CONFIG;if(!SC||!SC.token||!window.fetch)return;
  var base='http://127.0.0.1:'+(+SC.port||8765),btn=$('syncbtn'),bar=$('syncbar'),timer=null,since=0,seenRun=false,hideT=null;
  btn.hidden=false;
  function req(m,p){return fetch(base+p,{method:m,headers:{'X-Sync-Token':SC.token},cache:'no-store'})
    .then(function(r){return r.json().catch(function(){return {}}).then(function(j){return {code:r.status,j:j}})})}
  function show(cls,msg,p){clearTimeout(hideT);bar.hidden=false;bar.className='syncbar'+(cls?' '+cls:'');$('syncmsg').textContent=msg;
    $('syncfill').style.width=Math.round(Math.max(0,Math.min(1,p||0))*100)+'%';btn.classList.toggle('spin',!cls)}
  function fade(ms){clearTimeout(hideT);hideT=setTimeout(function(){bar.hidden=true},ms)}
  function stop(){clearInterval(timer);timer=null;btn.classList.remove('spin')}
  function fail(e,code){stop();show('err',code===401||code===403?T('syncAuth'):e?T('syncFail',{msg:e}):T('syncNoHelper'))}
  function fresh(s){var t=Date.parse(s.started||'');return seenRun||(!isNaN(t)&&t>=since-10000)}
  function done(s){stop();var n=+s.added_messages||0,m=+s.new_media||0,c=+s.new_chats||0;
    show('ok',T(c?'syncDoneChats':'syncDone',{n:n,m:m,c:c}));fade(20000)}
  function onStatus(s){
    if(s.state==='running'){seenRun=true;show('',T('syncRun',{step:s.step||'…',p:Math.round((+s.progress||0)*100)}),s.progress);return}
    if(!fresh(s))return; // previous run's result; wait for this run to report
    if(s.state==='done')done(s);else if(s.state==='failed')fail(s.message||'?')}
  function poll(){req('GET','/status').then(function(r){if(r.code!==200)return fail(r.j.error,r.code);onStatus(r.j)},function(){fail()})}
  function watch(){if(timer)return;timer=setInterval(poll,2000);poll()}
  btn.addEventListener('click',function(){
    if(timer)return;since=Date.now();seenRun=false;show('',T('syncStarting'),0);
    req('POST','/sync').then(function(r){
      if(r.code===202)return watch();
      if(r.code===409){show('',T('syncBusy'),0);seenRun=true;return watch()}
      fail(r.j.error,r.code)},function(){fail()})});
  // on load: pick up a sync started elsewhere (Dock app, 03:00 job) or a result from just before a reload
  req('GET','/status').then(function(r){if(r.code!==200)return;var s=r.j,f=Date.parse(s.finished||'');
    if(s.state==='running'){seenRun=true;watch()}else if(s.state==='done'&&!isNaN(f)&&Date.now()-f<120000)done(s)},function(){});
})();

// ---- auto-refresh after a rebuild: poll build_stamp.js via a <script> tag (fetch is blocked on file://).
// On change: save chat/scroll/tab/query to sessionStorage and reload; never while the lightbox,
// fullscreen or a text field is in use (retried on every poll until idle).
var STAMP0=window.__BUILD_STAMP||null,stale=false;
function busy(){var a=document.activeElement;return !$('lb').hidden||!!document.fullscreenElement||!!(a&&/^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName))}
function saveState(){var b=$('msgs'),st={t:Date.now()};
  if(chat&&!$('chat').hidden){st.id=chat.id;st.name=chat.name;st.inchat=document.body.classList.contains('inchat');
    st.tab=$('msgs').hidden?'media':'msg';st.tools=$('chat').classList.contains('toolsopen');st.q=$('search').value;st.mgs=$('mediagrid').scrollTop;
    st.bottom=end>=msgs.length&&b.scrollTop+b.clientHeight>b.scrollHeight-40;
    if(!st.bottom){var top=b.getBoundingClientRect().top,rows=b.querySelectorAll('.row[id],.sys[id],[id^="m"]');
      for(var i=0;i<rows.length;i++){if(!/^m\d+$/.test(rows[i].id))continue;var r=rows[i].getBoundingClientRect();if(r.bottom>top){st.mi=+rows[i].id.slice(1);st.off=r.top-top;break}}
      st.start=start}}
  try{sessionStorage.setItem('sb.refresh',JSON.stringify(st))}catch(e){}}
function tryRefresh(){if(stale&&!busy()){saveState();location.reload()}}
function pollStamp(){var s=document.createElement('script');s.src='build_stamp.js?t='+Date.now();
  s.onload=s.onerror=function(){s.remove();var v=window.__BUILD_STAMP;if(v){if(STAMP0==null)STAMP0=v;else if(v!==STAMP0)stale=true}tryRefresh()};
  document.head.appendChild(s)}
setInterval(pollStamp,30000);
function restoreState(){var st=null;try{st=JSON.parse(sessionStorage.getItem('sb.refresh')||'null');sessionStorage.removeItem('sb.refresh')}catch(e){}
  if(!st||Date.now()-st.t>120000||st.id==null)return;
  var id=null;INDEX.forEach(function(c){if(id==null&&c.name===st.name)id=c.id});
  if(id==null)return;openChat(id);
  (function w(){if(!chat||chat.id!==id)return setTimeout(w,50);
    if(!st.inchat)document.body.classList.remove('inchat');
    if(st.tools||st.q){tools(true,!!st.q);if(st.q){$('search').value=st.q;$('search').blur()}}
    if(st.tab==='media'){tools(true,false);showTab('media');$('mediagrid').scrollTop=st.mgs||0}
    else if(!st.bottom&&st.mi!=null&&st.mi<msgs.length){
      stick=0;start=Math.max(0,Math.min(st.start||0,st.mi));end=Math.min(msgs.length,Math.max(st.mi+150,start+CHUNK));renderMsgs(false);
      var b=$('msgs'),e=$('m'+st.mi);if(e)b.scrollTop+=e.getBoundingClientRect().top-b.getBoundingClientRect().top-(st.off||0)}
  })()}
if(!q)restoreState();
})();
