(()=>{
  'use strict';
  const root=document.querySelector('main')||document.body;
  if(!root)return;

  function cleanText(value){
    return String(value||'')
      .replace(/Warstwa ciągłej ekspozycji pozostaje wyłącznie eksperymentem paper[-\s]?trading\.?/gi,'Warstwa ciągłej ekspozycji jest częścią BriefRooms Trading Engine.')
      .replace(/The continuous-exposure layer remains an experimental paper[-\s]?trading exercise only\.?/gi,'The continuous-exposure layer is part of BriefRooms Trading Engine.')
      .replace(/Moduł badawczy\s*\/\s*paper[-\s]?trading/gi,'Moduł analityczny BriefRooms Trading Engine')
      .replace(/research\s*\/\s*paper[-\s]?trading/gi,'BriefRooms Trading Engine research')
      .replace(/paper[\s-]*trading/gi,'BriefRooms Trading Engine')
      .replace(/paper[\s_-]*only/gi,'BriefRooms Trading Engine');
  }

  function sanitize(node){
    if(!node)return;
    if(node.nodeType===Node.TEXT_NODE){
      const next=cleanText(node.nodeValue);
      if(next!==node.nodeValue)node.nodeValue=next;
      return;
    }
    if(node.nodeType!==Node.ELEMENT_NODE)return;
    if(['SCRIPT','STYLE','NOSCRIPT','TEXTAREA'].includes(node.tagName))return;
    const walker=document.createTreeWalker(node,NodeFilter.SHOW_TEXT);
    let textNode;
    while((textNode=walker.nextNode())){
      const next=cleanText(textNode.nodeValue);
      if(next!==textNode.nodeValue)textNode.nodeValue=next;
    }
  }

  sanitize(root);
  const observer=new MutationObserver(records=>{
    for(const record of records){
      for(const node of record.addedNodes)sanitize(node);
    }
  });
  observer.observe(root,{childList:true,subtree:true});
  window.addEventListener('pagehide',()=>observer.disconnect(),{once:true});
})();
