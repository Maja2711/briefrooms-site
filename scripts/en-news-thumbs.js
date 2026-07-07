(function(){
  function escUrl(s){return String(s||'').replace(/&amp;/g,'&')}
  function indexImages(data){
    var map={};
    ['latest','radar'].forEach(function(k){(data[k]||[]).forEach(function(it){if(it.link&&it.image)map[escUrl(it.link)]=it.image})});
    return map;
  }
  function apply(map){
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
