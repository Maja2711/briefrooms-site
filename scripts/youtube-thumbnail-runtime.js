(function(root){
  'use strict';
  if(!root||!root.document||root.__BR_YOUTUBE_THUMBNAILS__) return;
  root.__BR_YOUTUBE_THUMBNAILS__=true;
  var doc=root.document;

  var CHANNEL_IMAGES={
    'kotwarty':'https://unavatar.io/youtube/kotwarty',
    'kanalzeropl':'https://unavatar.io/youtube/KanalZeroPL',
    'nawschododbliskiegowschodu':'https://unavatar.io/youtube/nawschododbliskiegowschodu',
    'strategyfuture':'https://unavatar.io/youtube/StrategyFuture',
    'oswosrodekstudiowwschodnich':'https://unavatar.io/youtube/OSWOsrodekstudiowwschodnich',
    'openai':'https://unavatar.io/youtube/OpenAI',
    'stanfordhai':'https://unavatar.io/youtube/StanfordHAI',
    'csis':'https://unavatar.io/youtube/csis'
  };

  function injectStyle(){
    if(doc.getElementById('br-youtube-thumbnail-runtime-style')) return;
    var style=doc.createElement('style');
    style.id='br-youtube-thumbnail-runtime-style';
    style.textContent=[
      '.youtube-pick.br-yt-enhanced{position:relative!important;padding-left:86px!important}',
      '.youtube-pick.br-yt-enhanced>.br-yt-runtime-thumb{position:absolute;left:17px;top:18px;width:54px;height:54px;border:2px solid rgba(225,249,244,.66);border-radius:50%;background:#fff;object-fit:cover;box-shadow:0 8px 20px rgba(0,0,0,.30)}',
      '.br-yt-card.br-yt-enhanced{position:relative!important;padding-left:108px!important}',
      '.br-yt-card.br-yt-enhanced>.br-yt-runtime-thumb{position:absolute;left:18px;top:18px;width:74px;height:54px;border:1px solid rgba(215,240,255,.25);border-radius:12px;background:#07131e;object-fit:cover;box-shadow:0 8px 20px rgba(0,0,0,.28)}',
      '.br-yt-card.br-yt-enhanced>.br-yt-runtime-thumb.is-channel{width:58px;height:58px;left:21px;border-radius:50%;background:#fff}',
      '@media(max-width:460px){.youtube-pick.br-yt-enhanced{padding-left:78px!important}.youtube-pick.br-yt-enhanced>.br-yt-runtime-thumb{left:15px;width:48px;height:48px}.br-yt-card.br-yt-enhanced{padding-left:96px!important}.br-yt-card.br-yt-enhanced>.br-yt-runtime-thumb{left:16px;width:66px;height:48px}.br-yt-card.br-yt-enhanced>.br-yt-runtime-thumb.is-channel{width:52px;height:52px;left:19px}}'
    ].join('');
    doc.head.appendChild(style);
  }

  function channelKey(url){
    var match=(url.pathname||'').match(/\/@([^/?#]+)/i);
    if(match) return match[1].toLowerCase();
    if(/OSWOsrodekstudiowwschodnich/i.test(url.pathname||'')) return 'oswosrodekstudiowwschodnich';
    return '';
  }

  function imageFor(href){
    try{
      var url=new URL(href,root.location&&root.location.href||'https://briefrooms.com/');
      var podcast=(url.pathname||'').match(/\/podcast\/([A-Za-z0-9_-]{11})(?:\/|$)/);
      if(podcast) return {src:'https://i.ytimg.com/vi/'+podcast[1]+'/hqdefault.jpg',channel:false};
      var watch=url.searchParams&&url.searchParams.get('v');
      if(watch&&/^[A-Za-z0-9_-]{11}$/.test(watch)) return {src:'https://i.ytimg.com/vi/'+watch+'/hqdefault.jpg',channel:false};
      var short=(url.pathname||'').match(/^\/([A-Za-z0-9_-]{11})(?:\/|$)/);
      if(/youtu\.be$/i.test(url.hostname)&&short) return {src:'https://i.ytimg.com/vi/'+short[1]+'/hqdefault.jpg',channel:false};
      var key=channelKey(url);
      if(key&&CHANNEL_IMAGES[key]) return {src:CHANNEL_IMAGES[key],channel:true};
    }catch(error){}
    return null;
  }

  function decorate(card){
    if(!card||card.dataset.brYtThumbReady==='1'||card.querySelector('.br-yt-runtime-thumb')) return;
    var info=imageFor(card.getAttribute('href')||'');
    if(!info) return;
    var img=doc.createElement('img');
    img.className='br-yt-runtime-thumb'+(info.channel?' is-channel':'');
    img.src=info.src;
    img.alt='';
    img.loading='lazy';
    img.decoding='async';
    img.referrerPolicy='no-referrer';
    img.addEventListener('error',function(){card.classList.remove('br-yt-enhanced');img.remove();},{once:true});
    card.classList.add('br-yt-enhanced');
    card.dataset.brYtThumbReady='1';
    card.insertBefore(img,card.firstChild);
  }

  function scan(rootNode){
    injectStyle();
    var scope=rootNode&&rootNode.querySelectorAll?rootNode:doc;
    Array.prototype.forEach.call(scope.querySelectorAll('a.youtube-pick[href*="youtube.com"],a.youtube-pick[href*="music.youtube.com"],a.br-yt-card[href*="youtube.com"],a.br-yt-card[href*="music.youtube.com"]'),decorate);
  }

  function start(){
    scan(doc);
    if(typeof MutationObserver==='function'){
      new MutationObserver(function(records){records.forEach(function(record){Array.prototype.forEach.call(record.addedNodes||[],function(node){if(node&&node.nodeType===1) scan(node);});});}).observe(doc.documentElement,{childList:true,subtree:true});
    }
  }

  root.BriefRoomsYouTubeThumbnails={scan:scan,imageFor:imageFor};
  if(doc.readyState==='loading') doc.addEventListener('DOMContentLoaded',start,{once:true}); else start();
})(typeof window!=='undefined'?window:this);
