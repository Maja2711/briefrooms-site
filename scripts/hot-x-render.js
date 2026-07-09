(function(){
  'use strict';

  function esc(s){
    return String(s == null ? '' : s).replace(/[&<>"']/g,function(m){
      return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m];
    });
  }

  function lang(){
    return (document.documentElement.getAttribute('lang') || 'pl').toLowerCase().slice(0,2);
  }

  function labels(){
    var pl = lang() === 'pl';
    return {
      source: pl ? 'Otwórz post na X →' : 'Open X post →',
      expand: pl ? 'Rozwiń cały post' : 'Expand full post',
      original: pl ? 'Post z X — oryginał' : 'X post — original',
      summary: pl ? 'Opis redakcyjny' : 'Editorial note'
    };
  }

  function isConcreteXPost(url){
    return /^https?:\/\/(?:x\.com|twitter\.com)\/[^\/\s]+\/status\/\d+/i.test(String(url || ''));
  }

  function exactPostText(item){
    return String(item.x_post_text_raw || item.x_post_text || '');
  }

  function previewText(text, max){
    if(text.length <= max) return text;
    return text.slice(0, max).replace(/\s+$/,'') + '…';
  }

  function renderText(item, L){
    var exact = exactPostText(item);
    if(exact){
      if(exact.length > 420){
        return '<p class="hot-x-mode">'+esc(L.original)+'</p>'+
          '<p class="hot-x-text">'+esc(previewText(exact, 420))+'</p>'+
          '<details class="hot-x-details"><summary>'+esc(L.expand)+'</summary><pre class="hot-x-full">'+esc(exact)+'</pre></details>';
      }
      return '<p class="hot-x-mode">'+esc(L.original)+'</p><pre class="hot-x-full hot-x-short">'+esc(exact)+'</pre>';
    }
    var isPl = lang() === 'pl';
    var summary = String((isPl ? item.summary_pl : item.summary_en) || item.summary_pl || item.summary_en || '');
    return '<p class="hot-x-mode">'+esc(L.summary)+'</p><p class="hot-x-text">'+esc(summary)+'</p>';
  }

  function render(items){
    var feed = document.querySelector('.source-feed');
    if(!feed) return;
    var visible = (items || []).filter(function(item){ return isConcreteXPost(item.tweet_url); });
    if(!visible.length){
      feed.innerHTML = '';
      return;
    }
    var L = labels();
    var isPl = lang() === 'pl';
    feed.innerHTML = visible.map(function(item){
      var title = String((isPl ? item.title_pl : item.title_en) || item.title_pl || item.title_en || 'Post z X');
      var label = String((isPl ? item.label_pl : item.label_en) || item.label_pl || item.label_en || 'X');
      var url = String(item.tweet_url || '');
      var img = item.image ? '<div class="tweet-img"><img src="'+esc(item.image)+'" alt="" loading="lazy" referrerpolicy="no-referrer"></div>' : '';
      return '<article class="source-card hot-tweet hot-x-card">'+
        '<a class="hot-x-card-link" href="'+esc(url)+'" target="_blank" rel="noopener noreferrer">'+img+'<div class="tweet-kicker">'+esc(label)+'</div><h3>'+esc(title)+'</h3></a>'+ renderText(item, L)+ '<a class="hot-x-source" href="'+esc(url)+'" target="_blank" rel="noopener noreferrer">'+esc(L.source)+'</a>'+ '</article>';
    }).join('');
  }

  function addCss(){
    if(document.getElementById('hot-x-render-style')) return;
    var css = '.hot-x-card-link{display:block;color:inherit;text-decoration:none}.hot-x-card-link:hover{text-decoration:none}.hot-x-mode{margin:8px 0 6px;font-size:11px;font-weight:900;color:#8ffff6;text-transform:uppercase;letter-spacing:.04em}.hot-x-text{white-space:pre-wrap;margin:0 0 10px;font-size:13px;line-height:1.45;color:#d6e5f1}.hot-x-full{white-space:pre-wrap;word-wrap:break-word;margin:0 0 10px;font:13px/1.45 inherit;color:#eaf6ff;background:rgba(255,255,255,.055);border:1px solid rgba(255,255,255,.10);border-radius:12px;padding:10px}.hot-x-short{background:rgba(255,255,255,.04)}.hot-x-details{margin:6px 0 10px}.hot-x-details summary{cursor:pointer;color:#8ffff6;font-weight:900;font-size:13px}.hot-x-source{display:inline-flex;margin-top:4px;color:#38d6c9;font-weight:950;font-size:13px}.hot-x-source:hover{text-decoration:underline}';
    var style = document.createElement('style');
    style.id = 'hot-x-render-style';
    style.textContent = css;
    document.head.appendChild(style);
  }

  function load(){
    addCss();
    fetch('/data/hot_tweets.json?v='+Date.now(), {cache:'no-store'})
      .then(function(r){ return r.ok ? r.json() : {items:[]}; })
      .then(function(data){ render(data.items || []); })
      .catch(function(){ render([]); });
  }

  if(document.readyState === 'loading') document.addEventListener('DOMContentLoaded', load);
  else load();
})();
