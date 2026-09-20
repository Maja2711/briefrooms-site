(()=>{
  'use strict';

  const lang=(document.documentElement.lang||'pl').toLowerCase().startsWith('en')?'en':'pl';
  const copy=lang==='pl'?{
    eyebrow:'Najnowsze briefy',
    heading:'Co dziś naprawdę ma znaczenie',
    promise:'BriefRooms filtruje informacyjny szum, analizuje konsekwencje i mierzy wyniki własnych modeli.',
    lab:'BriefRooms Lab — modele, testy i wyniki',
    shareTitle:'BriefRooms Ci się przydał? Podaj dalej.',
    shareText:'Krótkie briefy, konkretne źródła i mniej informacyjnego szumu.',
    engineWeekly:'BriefRooms Trading Engine · WEEKLY',
    engineStock:'BriefRooms Stock Trading · OPEN',
    thoughtLabel:'Myśl AXIOM-a'
  }:{
    eyebrow:'Latest briefs',
    heading:'What really matters today',
    promise:'BriefRooms filters information noise, analyses consequences and measures the results of its own models.',
    lab:'BriefRooms Lab — models, tests and results',
    shareTitle:'Found BriefRooms useful? Share it.',
    shareText:'Concise briefs, concrete sources and less information noise.',
    engineWeekly:'BriefRooms Trading Engine · WEEKLY',
    engineStock:'BriefRooms Stock Trading · OPEN',
    thoughtLabel:'AXIOM thought'
  };

  const fallbackThought={
    pl:'„Przyszłość rzadko zaczyna się od wielkiego przełomu — częściej od jednej decyzji, której nikt poza tobą jeszcze nie rozumie.”',
    en:'“The future rarely begins with a great breakthrough — more often, it begins with one decision that nobody but you understands yet.”',
    author:'AXIOM',
    brand:'BriefRooms'
  };
  let thought=fallbackThought;

  function setText(node,value){
    if(node&&node.textContent!==value)node.textContent=value;
  }

  function ensureStyles(){
    if(document.getElementById('home-intelligence-layout-style'))return;
    const style=document.createElement('style');
    style.id='home-intelligence-layout-style';
    style.textContent=[
      '.home-intelligence-eyebrow{display:block;margin:0 0 7px;color:#75eee5;font-size:10px;font-weight:950;letter-spacing:.08em;text-transform:uppercase}',
      '.home-intelligence-promise{max-width:760px;margin:10px 0 0;color:#9fb2c8;font-size:13px;line-height:1.5}',
      '.main-head{position:relative}',
      '.axiom-thought{position:absolute;top:28px;right:0;width:min(440px,46%);text-align:right;pointer-events:none;z-index:2}',
      '.axiom-thought blockquote{margin:0}',
      '.axiom-thought__quote{margin:0;color:#8EC5FF;font-size:14px;font-weight:650;line-height:1.48;font-style:italic;text-wrap:balance;text-shadow:0 1px 12px rgba(0,0,0,.18)}',
      '.axiom-thought__signature{display:flex;justify-content:flex-end;align-items:baseline;gap:7px;margin-top:7px;font-style:normal}',
      '.axiom-thought__signature strong{color:#8ffff6;font-size:10px;font-weight:950;letter-spacing:.08em}',
      '.axiom-thought__signature small{color:#7f93a8;font-size:9px;letter-spacing:.02em}',
      '.br-share-strip.br-share-strip--footer{margin:30px 0 0}',
      '@media(max-width:1240px){.axiom-thought{position:static;width:auto;max-width:680px;margin:0 0 16px auto;text-align:left}.axiom-thought__signature{justify-content:flex-start}}',
      '@media(max-width:680px){.home-intelligence-promise{font-size:12px}.axiom-thought{margin:0 0 14px}.axiom-thought__quote{font-size:13px}.br-share-strip.br-share-strip--footer{margin-top:22px}}'
    ].join('');
    document.head.appendChild(style);
  }

  function applyHeading(){
    const box=document.querySelector('.main-head .section-head > div');
    const heading=box&&box.querySelector('h1');
    if(!box||!heading)return;
    setText(heading,copy.heading);
    let eyebrow=box.querySelector('.home-intelligence-eyebrow');
    if(!eyebrow){
      eyebrow=document.createElement('span');
      eyebrow.className='home-intelligence-eyebrow';
      box.insertBefore(eyebrow,heading);
    }
    setText(eyebrow,copy.eyebrow);
    let promise=box.querySelector('.home-intelligence-promise');
    if(!promise){
      promise=document.createElement('p');
      promise.className='home-intelligence-promise';
      heading.insertAdjacentElement('afterend',promise);
    }
    setText(promise,copy.promise);
  }

  function applyThought(){
    const head=document.querySelector('.main-head');
    if(!head)return;
    let card=head.querySelector('.axiom-thought');
    if(!card){
      card=document.createElement('aside');
      card.className='axiom-thought';
      card.setAttribute('aria-label',copy.thoughtLabel);
      const quote=document.createElement('blockquote');
      const text=document.createElement('p');
      text.className='axiom-thought__quote';
      const cite=document.createElement('cite');
      cite.className='axiom-thought__signature';
      const author=document.createElement('strong');
      const brand=document.createElement('small');
      cite.append(author,brand);
      quote.append(text,cite);
      card.appendChild(quote);
      head.appendChild(card);
    }
    setText(card.querySelector('.axiom-thought__quote'),thought[lang]||fallbackThought[lang]);
    setText(card.querySelector('.axiom-thought__signature strong'),thought.author||'AXIOM');
    setText(card.querySelector('.axiom-thought__signature small'),thought.brand||'BriefRooms');
  }

  function applyLabTitle(){
    setText(document.getElementById('home-lab-title'),copy.lab);
  }

  function moveShareToFooter(){
    const share=document.querySelector('.br-share-strip');
    const page=document.querySelector('.page');
    const footer=page&&page.querySelector(':scope > footer');
    if(!share||!page||!footer)return;
    setText(share.querySelector('.br-share-copy strong'),copy.shareTitle);
    setText(share.querySelector('.br-share-copy span'),copy.shareText);
    share.classList.add('br-share-strip--footer');
    if(share.parentNode!==page||share.nextElementSibling!==footer){
      page.insertBefore(share,footer);
    }
  }

  function normalizeTradingSignal(){
    const signal=document.getElementById('home-market-signal');
    const head=document.querySelector('.main-head');
    if(!signal||!head)return;

    const name=String(signal.querySelector('.home-market-signal__name')?.textContent||'').toUpperCase();
    const kind=String(signal.getAttribute('data-signal-kind')||'').toLowerCase();
    const href=String(signal.getAttribute('href')||'').toLowerCase();

    const canonicalWeekly=
      kind==='weekly' ||
      href.includes('pozycje-tygodniowe') ||
      href.includes('open-weekly-positions') ||
      name.includes('EUR/USD') ||
      name.includes('EURUSD') ||
      name.includes('S&P 500') ||
      name.includes('SP500') ||
      name.includes('BTC/USD') ||
      name.includes('BTCUSD') ||
      name.includes('BTC-USD');

    setText(
      signal.querySelector('.home-market-signal__kicker'),
      canonicalWeekly?copy.engineWeekly:copy.engineStock
    );

    if(signal.parentNode!==head)head.appendChild(signal);
  }

  function apply(){
    ensureStyles();
    applyHeading();
    applyThought();
    applyLabTitle();
    moveShareToFooter();
    normalizeTradingSignal();
  }

  async function loadThought(){
    try{
      const response=await fetch(`/data/home/axiom-thought.json?v=${Date.now()}`,{cache:'no-store'});
      if(!response.ok)return;
      const data=await response.json();
      if(data&&typeof data==='object'){
        thought={
          pl:typeof data.pl==='string'&&data.pl.trim()?data.pl.trim():fallbackThought.pl,
          en:typeof data.en==='string'&&data.en.trim()?data.en.trim():fallbackThought.en,
          author:typeof data.author==='string'&&data.author.trim()?data.author.trim():'AXIOM',
          brand:typeof data.brand==='string'&&data.brand.trim()?data.brand.trim():'BriefRooms'
        };
        applyThought();
      }
    }catch(_){
      // Fail closed to the embedded first thought; the rest of the homepage remains untouched.
    }
  }

  apply();
  loadThought();
  if(typeof MutationObserver==='function'){
    const observer=new MutationObserver(()=>apply());
    observer.observe(document.body,{childList:true,subtree:true});
    window.addEventListener('pagehide',()=>observer.disconnect(),{once:true});
  }
})();
