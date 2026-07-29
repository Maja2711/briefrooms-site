(function(root){
  'use strict';

  function sourceToImageHosts(){
    var result={};
    if(!root.document) return result;
    var node=root.document.getElementById('br-external-media-policy-data');
    if(!node) return result;
    try{
      var payload=JSON.parse(node.textContent||'{}');
      var configured=payload&&payload.source_to_image_hosts;
      if(!configured||typeof configured!=='object') return result;
      Object.keys(configured).forEach(function(source){
        if(!Array.isArray(configured[source])) return;
        result[String(source).toLowerCase()]=configured[source].map(function(host){
          return String(host).toLowerCase();
        });
      });
    }catch(error){}
    return result;
  }
  var SOURCE_TO_IMAGE_HOSTS = sourceToImageHosts();
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
