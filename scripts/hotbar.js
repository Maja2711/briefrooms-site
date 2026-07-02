(function () {
  'use strict';

  function getLang() {
    var lang = (document.documentElement.getAttribute('lang') || '').toLowerCase();
    return lang.indexOf('en') === 0 || location.pathname.indexOf('/en/') === 0 ? 'en' : 'pl';
  }

  function getItems(lang) {
    if (lang === 'en') {
      return [
        ['Latest news briefs', '/en/news.html'],
        ['Geopolitics, health, science and investing summaries', '/en/'],
        ['Open the News room for current updates', '/en/news.html']
      ];
    }
    return [
      ['Najnowsze aktualności i krótkie briefy', '/pl/aktualnosci.html'],
      ['Geopolityka, zdrowie, nauka i inwestycje w krótkiej formie', '/pl/'],
      ['Kliknij Aktualności, aby zobaczyć bieżące podsumowania', '/pl/aktualnosci.html']
    ];
  }

  function addGlassAiBadge() {
    if (document.getElementById('br-glass-ai-badge-style')) return;
    var css = `
      .brand{align-items:flex-start!important;gap:9px!important}
      .ai-badge{position:relative!important;display:inline-flex!important;align-items:center!important;gap:5px!important;margin-top:-4px!important;padding:4px 7px 4px 6px!important;min-height:22px!important;border-radius:999px!important;overflow:hidden!important;white-space:nowrap!important;color:rgba(218,255,250,.92)!important;font-size:8.5px!important;font-weight:720!important;letter-spacing:.045em!important;line-height:1!important;text-transform:none!important;border:1px solid rgba(255,255,255,.16)!important;background:linear-gradient(180deg,rgba(255,255,255,.18),rgba(255,255,255,.05)),linear-gradient(135deg,rgba(56,214,201,.10),rgba(56,214,201,.025)),rgba(10,24,36,.28)!important;box-shadow:0 5px 16px rgba(0,0,0,.18),inset 0 1px 0 rgba(255,255,255,.22),inset 0 -1px 0 rgba(255,255,255,.04)!important;backdrop-filter:blur(16px) saturate(165%)!important;-webkit-backdrop-filter:blur(16px) saturate(165%)!important;text-shadow:0 0 7px rgba(210,255,250,.18)!important}
      .ai-badge:before{content:""!important;position:absolute!important;inset:1px!important;border-radius:999px!important;pointer-events:none!important;background:linear-gradient(115deg,rgba(255,255,255,.24) 0%,rgba(255,255,255,.10) 20%,rgba(255,255,255,.02) 46%,rgba(255,255,255,0) 100%)!important}
      .ai-dot{position:relative!important;z-index:1!important;width:5px!important;height:5px!important;border-radius:50%!important;flex:0 0 auto!important;background:linear-gradient(180deg,#7dfff4,#2bdacb)!important;box-shadow:0 0 0 2px rgba(56,214,201,.07),0 0 8px rgba(56,214,201,.24)!important}
      .ai-badge span:last-child{position:relative!important;z-index:1!important}
    `;
    var style = document.createElement('style');
    style.id = 'br-glass-ai-badge-style';
    style.textContent = css;
    document.head.appendChild(style);
  }

  function addTicker() {
    var track = document.getElementById('br-hotbar-track');
    if (!track) return;

    var lang = getLang();
    var items = getItems(lang);
    track.innerHTML = '';

    function addSet() {
      items.forEach(function (item) {
        var link = document.createElement('a');
        link.className = 'br-hotbar-item';
        link.href = item[1];
        link.textContent = item[0];
        track.appendChild(link);

        var sep = document.createElement('span');
        sep.className = 'br-hotbar-sep';
        sep.textContent = ' • ';
        track.appendChild(sep);
      });
    }

    addSet();
    addSet();

    var time = document.getElementById('br-hotbar-time');
    if (time) {
      time.textContent = new Date().toLocaleTimeString(lang === 'en' ? 'en-GB' : 'pl-PL', { hour: '2-digit', minute: '2-digit' });
    }

    window.requestAnimationFrame(function () {
      var distance = Math.max(600, track.scrollWidth / 2);
      track.style.setProperty('--br-hotbar-distance', '-' + distance + 'px');
      track.style.setProperty('--br-hotbar-duration', '34s');
      track.classList.add('is-ready');
    });
  }

  function addEnglishDoorStyles() {
    if (getLang() !== 'en' || location.pathname.indexOf('/en/') !== 0) return;
    if (document.getElementById('br-en-premium-doors')) return;

    var css = `
      .rooms .room-grid{display:flex!important;flex-wrap:nowrap!important;gap:14px!important;justify-content:space-between!important;align-items:stretch!important;width:100%!important}
      .rooms .room-door{position:relative!important;display:block!important;flex:0 0 calc((100% - 56px)/5)!important;min-width:0!important;height:318px!important;min-height:318px!important;border-radius:24px!important;overflow:hidden!important;text-decoration:none!important;color:#eaf6ff!important;background:linear-gradient(180deg,rgba(255,255,255,.20),rgba(255,255,255,.045))!important;border:1px solid rgba(255,255,255,.22)!important;box-shadow:0 28px 72px rgba(0,0,0,.42),0 8px 24px rgba(0,0,0,.18),inset 0 1px 0 rgba(255,255,255,.22),inset 0 -24px 36px rgba(255,255,255,.03)!important;backdrop-filter:blur(22px) saturate(185%)!important;-webkit-backdrop-filter:blur(22px) saturate(185%)!important;transition:transform .22s ease,box-shadow .22s ease,border-color .22s ease!important;align-items:stretch!important;justify-content:initial!important;padding:0!important}
      .rooms .room-door:hover{transform:translateY(-6px) scale(1.01)!important;border-color:rgba(255,255,255,.28)!important;box-shadow:0 34px 88px rgba(0,0,0,.48),0 10px 28px rgba(0,0,0,.22),inset 0 1px 0 rgba(255,255,255,.26),inset 0 -24px 36px rgba(255,255,255,.04)!important}
      .rooms .room-door:before{content:""!important;position:absolute!important;inset:0!important;z-index:0!important;pointer-events:none!important;background:linear-gradient(180deg,rgba(255,255,255,.22),rgba(255,255,255,0) 30%),radial-gradient(circle at 50% 0%,rgba(255,255,255,.28),rgba(255,255,255,0) 46%),linear-gradient(90deg,rgba(255,255,255,.06),rgba(255,255,255,0) 25%,rgba(255,255,255,.04) 60%,rgba(255,255,255,0))!important}
      .rooms .portal{position:absolute!important;left:13px!important;right:13px!important;top:13px!important;bottom:14px!important;display:block!important;border-radius:90px 90px 16px 16px!important;overflow:hidden!important;border:1px solid rgba(255,255,255,.26)!important;background:linear-gradient(180deg,rgba(255,255,255,.26),rgba(255,255,255,.04))!important;box-shadow:inset 0 1px 0 rgba(255,255,255,.28),inset 0 0 0 2px rgba(255,255,255,.06),inset 0 -10px 20px rgba(0,0,0,.08),0 20px 46px rgba(0,0,0,.34)!important;transform:perspective(1000px) rotateX(5deg)!important}
      .rooms .portal:before{content:""!important;position:absolute!important;inset:8px 8px 12px!important;border-radius:78px 78px 12px 12px!important;background:linear-gradient(180deg,rgba(255,255,255,.58) 0%,rgba(255,255,255,.28) 16%,rgba(255,255,255,.10) 32%,rgba(10,22,38,.38) 68%,rgba(4,12,22,.84) 100%)!important;box-shadow:inset 0 1px 0 rgba(255,255,255,.24),inset 24px 0 34px rgba(255,255,255,.05),inset -20px 0 30px rgba(0,0,0,.16),inset 0 -18px 24px rgba(0,0,0,.10)!important}
      .rooms .portal:after{content:""!important;position:absolute!important;left:18px!important;right:18px!important;top:10px!important;height:40%!important;border-radius:56px!important;background:linear-gradient(180deg,rgba(255,255,255,.52),rgba(255,255,255,.08) 48%,rgba(255,255,255,0))!important;opacity:1!important;filter:blur(.5px)!important}
      .rooms .room-content{position:relative!important;z-index:2!important;display:flex!important;height:100%!important;flex-direction:column!important;align-items:center!important;text-align:center!important;padding:46px 14px 18px!important;width:100%!important;box-sizing:border-box!important}
      .rooms .room-icon{display:block!important;font-size:23px!important;line-height:1!important;margin:0 0 74px!important;text-shadow:0 0 12px currentColor,0 0 28px currentColor!important}
      .rooms .room-title,.rooms .room-door h3{display:block!important;font-size:18px!important;line-height:1.1!important;font-weight:900!important;letter-spacing:-.02em!important;color:#f4fbff!important;text-shadow:0 2px 18px rgba(0,0,0,.55),0 0 8px rgba(255,255,255,.10)!important;margin:0 0 10px!important}
      .rooms .room-pill,.rooms .room-door small{display:inline-flex!important;align-items:center!important;justify-content:center!important;padding:5px 10px!important;border-radius:999px!important;font-size:11px!important;line-height:1!important;font-weight:900!important;background:rgba(255,255,255,.13)!important;border:1px solid rgba(255,255,255,.16)!important;box-shadow:inset 0 1px 0 rgba(255,255,255,.18)!important;margin:0 0 12px!important;color:inherit!important}
      .rooms .room-desc,.rooms .room-door p{display:block!important;min-height:40px!important;font-size:12px!important;line-height:1.35!important;color:#d6e5f1!important;margin:0 0 12px!important}
      .rooms .open{display:block!important;margin-top:auto!important;font-size:12px!important;line-height:1!important;font-weight:900!important;color:inherit!important;text-shadow:0 0 14px currentColor,0 0 28px currentColor,0 0 42px currentColor!important}
      .rooms .room-door:nth-child(1),.rooms .room-door:nth-child(1) .room-icon,.rooms .room-door:nth-child(1) .room-pill,.rooms .room-door:nth-child(1) small,.rooms .room-door:nth-child(1) .open{color:#63fff4!important}
      .rooms .room-door:nth-child(2),.rooms .room-door:nth-child(2) .room-icon,.rooms .room-door:nth-child(2) .room-pill,.rooms .room-door:nth-child(2) small,.rooms .room-door:nth-child(2) .open{color:#ffd15e!important}
      .rooms .room-door:nth-child(3),.rooms .room-door:nth-child(3) .room-icon,.rooms .room-door:nth-child(3) .room-pill,.rooms .room-door:nth-child(3) small,.rooms .room-door:nth-child(3) .open{color:#86ffb7!important}
      .rooms .room-door:nth-child(4),.rooms .room-door:nth-child(4) .room-icon,.rooms .room-door:nth-child(4) .room-pill,.rooms .room-door:nth-child(4) small,.rooms .room-door:nth-child(4) .open{color:#7fc8ff!important}
      .rooms .room-door:nth-child(5),.rooms .room-door:nth-child(5) .room-icon,.rooms .room-door:nth-child(5) .room-pill,.rooms .room-door:nth-child(5) small,.rooms .room-door:nth-child(5) .open{color:#e1a2ff!important}
      @media(max-width:980px){.rooms .room-grid{display:grid!important;grid-template-columns:repeat(3,minmax(0,1fr))!important;gap:16px!important}.rooms .room-door{height:300px!important;min-height:300px!important;flex:none!important}}
      @media(max-width:700px){.rooms .room-grid{grid-template-columns:repeat(2,minmax(0,1fr))!important}}
      @media(max-width:520px){.rooms .room-grid{grid-template-columns:1fr!important}}
    `;
    var style = document.createElement('style');
    style.id = 'br-en-premium-doors';
    style.textContent = css;
    document.head.appendChild(style);
  }

  function init() {
    addGlassAiBadge();
    addEnglishDoorStyles();
    addTicker();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
