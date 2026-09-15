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
    engine:'BriefRooms Trading Engine · WEEKLY'
  }:{
    eyebrow:'Latest briefs',
    heading:'What really matters today',
    promise:'BriefRooms filters information noise, analyses consequences and measures the results of its own models.',
    lab:'BriefRooms Lab — models, tests and results',
    shareTitle:'Found BriefRooms useful? Share it.',
    shareText:'Concise briefs, concrete sources and less information noise.',
    engine:'BriefRooms Trading Engine · WEEKLY'
  };

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
      '.br-share-strip.br-share-strip--footer{margin:30px 0 0}',
      '@media(max-width:680px){.home-intelligence-promise{font-size:12px}.br-share-strip.br-share-strip--footer{margin-top:22px}}'
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
    setText(signal.querySelector('.home-market-signal__kicker'),copy.engine);
    if(signal.parentNode!==head)head.appendChild(signal);
  }

  function apply(){
    ensureStyles();
    applyHeading();
    applyLabTitle();
    moveShareToFooter();
    normalizeTradingSignal();
  }

  apply();
  if(typeof MutationObserver==='function'){
    const observer=new MutationObserver(()=>apply());
    observer.observe(document.body,{childList:true,subtree:true});
    window.addEventListener('pagehide',()=>observer.disconnect(),{once:true});
  }
})();
