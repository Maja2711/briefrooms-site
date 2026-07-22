(function(){
  'use strict';

  var TOTAL_ITEMS = 10;
  var INITIAL_VISIBLE_ITEMS = 3;
  var TRACKING_PARAMS = /^(?:src|ref|ref_src|s|t|twclid|utm_.+)$/i;

  function esc(value){
    return String(value == null ? '' : value).replace(/[&<>"']/g,function(char){
      return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char];
    });
  }

  function lang(){
    return (document.documentElement.getAttribute('lang') || 'pl').toLowerCase().slice(0,2);
  }

  function labels(){
    var pl = lang() === 'pl';
    return {
      comment: pl ? 'Komentarz' : 'Comment',
      more: pl ? 'Więcej z X' : 'More from X',
      less: pl ? 'Pokaż mniej' : 'Show less',
      postType: pl ? 'Konkretny post' : 'Specific post',
      postCta: pl ? 'Otwórz post na X →' : 'Open post on X →',
      unavailableTitle: pl ? 'Posty z X są chwilowo niedostępne' : 'X posts are temporarily unavailable',
      unavailableText: pl ? 'Nie publikujemy zastępczych linków ani nieistniejących wątków. Sekcja wróci po pobraniu zweryfikowanych, bezpośrednich postów z X.' : 'We do not publish substitute search links or nonexistent threads. The section will return after verified direct X posts are fetched.'
    };
  }

  function cleanXUrl(value){
    try{
      var url = new URL(String(value || ''));
      if(!/^(?:x\.com|www\.x\.com|twitter\.com|www\.twitter\.com)$/i.test(url.hostname)) return '';
      if(!/^https?:$/.test(url.protocol)) return '';
      var path = url.pathname.replace(/\/{2,}/g,'/').replace(/\/$/,'') || '/';
      if(!(/^\/[^/\s]+\/status\/\d+$/i.test(path) || /^\/(?:search|explore)$/i.test(path))) return '';
      Array.from(url.searchParams.keys()).forEach(function(key){
        if(TRACKING_PARAMS.test(key)) url.searchParams.delete(key);
      });
      url.protocol = 'https:';
      url.hostname = 'x.com';
      url.port = '';
      url.pathname = path;
      url.hash = '';
      return url.toString().replace(/\?$/,'');
    }catch(error){
      return '';
    }
  }

  function isConcreteXPost(value){
    var url = cleanXUrl(value);
    return /^https:\/\/x\.com\/[^/\s]+\/status\/\d+(?:\?|$)/i.test(url);
  }

  function itemUrl(item){
    var direct = cleanXUrl(item && item.tweet_url);
    if(isConcreteXPost(direct)) return direct;
    return '';
  }

  function normalizedTitle(value){
    return String(value || '').toLowerCase().normalize('NFKD').replace(/[\u0300-\u036f]/g,'').replace(/[^a-z0-9]+/g,'');
  }

  function languageValue(item, field){
    return String(item[field+'_'+lang()] || '').trim();
  }

  function commentText(item){
    return languageValue(item, 'comment') || languageValue(item, 'summary');
  }

  function fingerprints(item){
    var ids = [];
    ['tweet_url','search_url'].forEach(function(field){
      var url = cleanXUrl(item[field]);
      if(url) ids.push('url:'+url);
    });
    ['title_en','title_pl'].forEach(function(field){
      var title = normalizedTitle(item[field]);
      if(title) ids.push(field+':'+title);
    });
    return ids;
  }

  function usableItems(items){
    var seen = {};
    var categories = {};
    var ordered = (items || []).map(function(item,index){return {item:item,index:index};});
    ordered.sort(function(a,b){
      var directDifference = Number(!isConcreteXPost(a.item && a.item.tweet_url)) - Number(!isConcreteXPost(b.item && b.item.tweet_url));
      return directDifference || a.index - b.index;
    });
    return ordered.reduce(function(result,entry){
      var item = entry.item;
      if(!item || typeof item !== 'object') return result;
      var title = languageValue(item, 'title');
      var comment = commentText(item);
      var url = itemUrl(item);
      var ids = fingerprints(item);
      var category = normalizedTitle(item.category) || 'other';
      if(!url || !title || comment.length < 40 || !ids.length) return result;
      if(ids.some(function(id){return seen[id];}) || (categories[category] || 0) >= 2) return result;
      ids.forEach(function(id){seen[id] = true;});
      categories[category] = (categories[category] || 0) + 1;
      result.push(item);
      return result;
    },[]).slice(0,TOTAL_ITEMS);
  }

  function cardHtml(item,index,L){
    var title = languageValue(item, 'title');
    var category = languageValue(item, 'label') || 'X';
    var url = itemUrl(item);
    var direct = isConcreteXPost(url);
    var image = item.image ? '<div class="tweet-img"><img src="'+esc(item.image)+'" alt="" loading="lazy" referrerpolicy="no-referrer"></div>' : '';
    var extra = index >= INITIAL_VISIBLE_ITEMS ? ' hot-x-extra' : '';
    return '<article class="source-card hot-tweet hot-x-card'+extra+'">'
      +'<a class="hot-x-card-link" href="'+esc(url)+'" target="_blank" rel="noopener noreferrer">'+image
      +'<div class="hot-x-badges"><span class="tweet-kicker">'+esc(category)+'</span><span class="hot-x-link-type">'+esc(L.postType)+'</span></div>'
      +'<h3>'+esc(title)+'</h3></a>'
      +'<p class="hot-x-mode">'+esc(L.comment)+'</p><p class="hot-x-text">'+esc(commentText(item))+'</p>'
      +'<a class="hot-x-source" href="'+esc(url)+'" target="_blank" rel="noopener noreferrer">'+esc(L.postCta)+'</a>'
      +'</article>';
  }

  function attachToggle(feed,button,L){
    button.addEventListener('click',function(){
      var expanded = !feed.classList.contains('hot-x-expanded');
      var section = feed.closest('.side') || feed;
      var sectionTop = section.getBoundingClientRect().top + window.scrollY;
      feed.classList.toggle('hot-x-expanded',expanded);
      button.setAttribute('aria-expanded',expanded ? 'true' : 'false');
      button.textContent = expanded ? L.less : L.more;
      if(!expanded && window.scrollY > sectionTop){
        var reducedMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        window.scrollTo({top:Math.max(0,sectionTop - 16),behavior:reducedMotion ? 'auto' : 'smooth'});
      }
    });
  }

  function moreButtonHtml(L){
    return '<div class="hot-x-more-wrap"><button type="button" class="hot-x-more" aria-expanded="false">'+esc(L.more)+'</button></div>';
  }

  function renderUnavailable(){
    var feed = document.querySelector('.source-feed');
    if(!feed) return false;
    var L = labels();
    feed.setAttribute('data-hot-x-count','0');
    feed.innerHTML = '<div class="source-card hot-x-unavailable" role="status"><h3>'+esc(L.unavailableTitle)+'</h3><p>'+esc(L.unavailableText)+'</p></div>';
    return true;
  }

  function render(items){
    var feed = document.querySelector('.source-feed');
    if(!feed) return false;
    var visible = usableItems(items);
    if(!visible.length) return false;
    var L = labels();
    var button = visible.length > INITIAL_VISIBLE_ITEMS ? moreButtonHtml(L) : '';
    feed.classList.remove('hot-x-expanded');
    feed.setAttribute('data-hot-x-count',String(visible.length));
    feed.innerHTML = visible.map(function(item,index){return cardHtml(item,index,L);}).join('') + button;
    var toggle = feed.querySelector('.hot-x-more');
    if(toggle) attachToggle(feed,toggle,L);
    return true;
  }

  function cacheKey(){
    return 'briefrooms_hot_x_last_good_v5_'+lang();
  }

  function saveLastGood(items){
    try{
      var good = usableItems(items).slice(0,TOTAL_ITEMS);
      if(good.length >= INITIAL_VISIBLE_ITEMS) localStorage.setItem(cacheKey(),JSON.stringify(good));
    }catch(error){}
  }

  function loadLastGood(){
    try{
      var raw = localStorage.getItem(cacheKey());
      var parsed = raw ? JSON.parse(raw) : [];
      return Array.isArray(parsed) ? usableItems(parsed).slice(0,TOTAL_ITEMS) : [];
    }catch(error){
      return [];
    }
  }

  function addCss(){
    if(document.getElementById('hot-x-render-style')) return;
    var css = '.source-feed{gap:10px}.hot-x-unavailable{padding:18px;border:1px solid rgba(255,190,92,.28);background:rgba(255,190,92,.07)}.hot-x-unavailable h3{margin:0 0 8px;font-size:16px;color:#f6d48b}.hot-x-unavailable p{margin:0;color:#b9c9d8;font-size:13px;line-height:1.5}.source-card.hot-tweet{display:block;overflow:hidden;padding:13px;border-radius:17px}.source-card.hot-tweet .tweet-img{height:92px;margin:-2px -2px 10px;border-radius:13px}.source-card.hot-tweet h3{margin:0 0 7px;font-size:16px;line-height:1.22}.hot-x-card-link{display:block;color:inherit;text-decoration:none}.hot-x-card-link:hover{text-decoration:none}.hot-x-badges{display:flex;align-items:center;flex-wrap:wrap;gap:6px;margin-bottom:8px}.source-card.hot-tweet .tweet-kicker{margin:0;padding:4px 8px;font-size:12px}.hot-x-link-type{display:inline-flex;border:1px solid rgba(255,255,255,.13);border-radius:999px;padding:4px 7px;color:#aebfd0;font-size:12px;font-weight:800}.hot-x-mode{margin:8px 0 4px;font-size:12px;font-weight:900;color:#8ffff6;text-transform:uppercase;letter-spacing:.04em}.source-card.hot-tweet .hot-x-text{display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:4;overflow:hidden;margin:0 0 8px;color:#c8d7e5;font-size:12.5px;line-height:1.42}.hot-x-source{display:inline-flex;margin-top:2px;color:#38d6c9;font-size:12px;font-weight:950}.hot-x-source:hover{text-decoration:underline}.source-card.hot-tweet.hot-x-extra{display:none;opacity:0;transform:translateY(-6px)}.hot-x-expanded .source-card.hot-tweet.hot-x-extra{display:block;animation:hot-x-reveal .2s ease both}.hot-x-more-wrap{display:flex;justify-content:center;padding-top:3px}.hot-x-more{display:inline-flex;align-items:center;justify-content:center;border:1px solid rgba(56,214,201,.30);border-radius:999px;padding:9px 15px;background:rgba(56,214,201,.09);box-shadow:0 10px 24px rgba(0,0,0,.18);color:#38d6c9;font:850 12px/1.2 inherit;cursor:pointer}.hot-x-more:hover{background:rgba(56,214,201,.16);color:#fff}.hot-x-more:focus-visible{outline:2px solid #8ffff6;outline-offset:3px}@keyframes hot-x-reveal{from{opacity:0;transform:translateY(-6px)}to{opacity:1;transform:translateY(0)}}@media(prefers-reduced-motion:reduce){.hot-x-expanded .source-card.hot-tweet.hot-x-extra{animation:none}.hot-x-more{scroll-behavior:auto}}';
    var style = document.createElement('style');
    style.id = 'hot-x-render-style';
    style.textContent = css;
    document.head.appendChild(style);
  }

  function renderEmergency(seed,cached){
    if(cached.length >= INITIAL_VISIBLE_ITEMS) return Promise.resolve(render(cached));
    return fetch('/data/hot_x_emergency.json?v='+Date.now(),{cache:'no-store'})
      .then(function(response){
        if(!response.ok) throw new Error('Hot X emergency HTTP '+response.status);
        return response.json();
      })
      .then(function(data){
        var emergency = Array.isArray(data.items) ? data.items : [];
        var combined = usableItems((seed || []).concat(cached || [],emergency));
        if(combined.length){
          render(combined);
          saveLastGood(combined);
          return true;
        }
        return renderUnavailable();
      })
      .catch(function(){
        var lastResort = usableItems((seed || []).concat(cached || []));
        return lastResort.length ? render(lastResort) : renderUnavailable();
      });
  }

  function load(){
    addCss();
    var cached = loadLastGood();
    fetch('/data/hot_tweets.json?v='+Date.now(),{cache:'no-store'})
      .then(function(response){
        if(!response.ok) throw new Error('Hot X HTTP '+response.status);
        return response.json();
      })
      .then(function(data){
        var items = Array.isArray(data.items) ? data.items : [];
        var good = usableItems(items);
        if(good.length >= INITIAL_VISIBLE_ITEMS && render(good)){
          saveLastGood(good);
          return;
        }
        return renderEmergency(good,cached);
      })
      .catch(function(){
        return renderEmergency([],cached);
      });
  }

  window.BriefRoomsHotX = {
    cleanXUrl: cleanXUrl,
    isConcreteXPost: isConcreteXPost,
    usableItems: usableItems,
    render: render,
    labels: labels,
    cardHtml: cardHtml,
    attachToggle: attachToggle,
    moreButtonHtml: moreButtonHtml,
    renderEmergency: renderEmergency,
    renderUnavailable: renderUnavailable
  };

  if(document.readyState === 'loading') document.addEventListener('DOMContentLoaded',load);
  else load();
})();
