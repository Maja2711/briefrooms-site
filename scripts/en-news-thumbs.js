(function(){
  function escUrl(s){return String(s||'').replace(/&amp;/g,'&')}
  function addStyle(){
    if(document.getElementById('en-news-thumbs-style'))return;
    var s=document.createElement('style');
    s.id='en-news-thumbs-style';
    s.textContent='.news-thumb.has-image{padding:0!important;overflow:hidden!important;background:rgba(255,255,255,.06)!important;border:1px solid rgba(255,255,255,.16)!important;align-items:stretch!important;justify-content:stretch!important}.news-thumb.has-image img{width:100%!important;height:100%!important;display:block!important;object-fit:cover!important}.news-thumb:not(.has-image) .title{max-width:58px!important;white-space:normal!important;overflow:hidden!important;text-overflow:clip!important;line-height:1.05!important}';
    document.head.appendChild(s);
  }
  function indexImages(data){
    var map={};
    ['latest','radar'].forEach(function(k){(data[k]||[]).forEach(function(it){if(it.link&&it.image)map[escUrl(it.link)]=it.image})});
    return map;
  }
  function apply(map){
    addStyle();
    document.querySelectorAll('.news-main-link').forEach(function(a){
      var box=a.querySelector('.news-thumb');
      if(!box || box.classList.contains('has-image'))return;
      var src=map[escUrl(a.href)]||'';
      if(!src)return;
      var img=document.createElement('img');
      img.alt='';img.loading='lazy';img.referrerPolicy='no-referrer';img.src=src;
      img.onerror=function(){box.classList.remove('has-image')};
      box.classList.add('has-image');box.textContent='';box.appendChild(img);
    });
  }
  fetch('/en/home_brief.json?v='+Date.now(),{cache:'no-store'}).then(function(r){return r.ok?r.json():{}}).then(function(data){apply(indexImages(data))});
})();
