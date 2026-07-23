(function(root){
  'use strict';

  var SOURCE_TO_IMAGE_HOSTS = {
    'tvn24.pl':['tvn24.pl'],
    'rmf24.pl':['rmf24.pl','rmffm.pl'],
    'polsatnews.pl':['polsatnews.pl','grupapolsatplus.pl'],
    'bankier.pl':['bankier.pl'],
    'businessinsider.com.pl':['businessinsider.com.pl','onet.pl','ocdn.eu'],
    'pap.pl':['pap.pl'],
    'naukawpolsce.pl':['naukawpolsce.pl','pap.pl'],
    'bbc.co.uk':['bbc.co.uk','bbc.com','bbci.co.uk'],
    'bbc.com':['bbc.co.uk','bbc.com','bbci.co.uk'],
    'reuters.com':['reuters.com','reutersmedia.net'],
    'apnews.com':['apnews.com'],
    'theguardian.com':['theguardian.com','guim.co.uk'],
    'who.int':['who.int'],
    'cdc.gov':['cdc.gov'],
    'nhs.uk':['nhs.uk'],
    'cochrane.org':['cochrane.org'],
    'nasa.gov':['nasa.gov'],
    'esa.int':['esa.int'],
    'sport.tvp.pl':['tvp.pl'],
    'polsatsport.pl':['polsatsport.pl','grupapolsatplus.pl'],
    'przegladsportowy.onet.pl':['onet.pl','ocdn.eu'],
    'sport.onet.pl':['onet.pl','ocdn.eu'],
    'sportowefakty.wp.pl':['wp.pl','wpcdn.pl'],
    'eurosport.tvn24.pl':['tvn24.pl'],
    'laczynaspilka.pl':['laczynaspilka.pl','pzpn.pl'],
    'pzpn.pl':['laczynaspilka.pl','pzpn.pl'],
    'atptour.com':['atptour.com'],
    'wtatennis.com':['wtatennis.com'],
    'fifa.com':['fifa.com'],
    'uefa.com':['uefa.com'],
    'espn.com':['espn.com','espncdn.com']
  };
  var BLOCKED_NAME = /(?:^|[-_./])(?:pixel|tracking|spacer|blank|beacon)(?:[-_./]|$)/i;

  function hostMatches(host,suffix){
    host=String(host||'').toLowerCase().replace(/^\.+|\.+$/g,'');
    suffix=String(suffix||'').toLowerCase().replace(/^\.+|\.+$/g,'');
    return Boolean(host&&suffix&&(host===suffix||host.endsWith('.'+suffix)));
  }

  function parse(value,base){
    try{return new URL(String(value||''),base||'https://briefrooms.com/');}
    catch(error){return null;}
  }

  function safeImageUrl(value,sourceUrl){
    var source=parse(sourceUrl);
    var image=parse(value,sourceUrl);
    if(!source||!image||image.protocol!=='https:'||image.username||image.password) return '';
    var path=image.pathname+(image.search||'');
    if(BLOCKED_NAME.test(path)) return '';
    var sourceHost=source.hostname.toLowerCase();
    var imageHost=image.hostname.toLowerCase();
    var allowed=[sourceHost];
    Object.keys(SOURCE_TO_IMAGE_HOSTS).forEach(function(sourceSuffix){
      if(hostMatches(sourceHost,sourceSuffix)) allowed=allowed.concat(SOURCE_TO_IMAGE_HOSTS[sourceSuffix]);
    });
    var sameFamily=hostMatches(imageHost,sourceHost)||hostMatches(sourceHost,imageHost);
    if(!sameFamily&&!allowed.some(function(host){return hostMatches(imageHost,host);})){return '';}
    image.hash='';
    return image.href;
  }

  function fallback(image){
    if(!image||image.dataset.brMediaFailed==='1') return;
    image.dataset.brMediaFailed='1';
    var frame=image.closest('.news-thumb,.thumb,.image');
    if(frame){
      frame.classList.remove('has-image');
      frame.classList.add('media-fallback-active');
      var badge=frame.querySelector('.media-source-badge');
      if(badge) badge.remove();
    }
    image.remove();
  }

  function guard(image){
    var sourceUrl=image.getAttribute('data-br-source-url')||'';
    var safe=safeImageUrl(image.getAttribute('src'),sourceUrl);
    if(!safe){fallback(image);return;}
    if(image.src!==safe) image.src=safe;
    image.addEventListener('error',function(){fallback(image);},{once:true});
  }

  function scan(rootNode){
    var scope=rootNode&&rootNode.querySelectorAll?rootNode:document;
    Array.prototype.forEach.call(scope.querySelectorAll('img[data-br-external-media="source-linked"]'),guard);
  }

  root.BriefRoomsMediaPolicy={safeImageUrl:safeImageUrl,guard:guard,scan:scan};
  if(root.document){
    var start=function(){
      scan(document);
      if(typeof MutationObserver==='function'){
        new MutationObserver(function(records){
          records.forEach(function(record){
            Array.prototype.forEach.call(record.addedNodes||[],function(node){
              if(node&&node.nodeType===1){
                if(node.matches&&node.matches('img[data-br-external-media="source-linked"]')) guard(node);
                scan(node);
              }
            });
          });
        }).observe(document.documentElement,{childList:true,subtree:true});
      }
    };
    if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',start,{once:true});
    else start();
  }
})(typeof globalThis!=='undefined'?globalThis:this);
