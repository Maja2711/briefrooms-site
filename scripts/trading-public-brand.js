(()=>{
  'use strict';
  const root=document.querySelector('main')||document.body;
  if(!root)return;
  const replacement='BriefRooms Trading Engine';
  const patterns=[/paper[\s-]*trading/gi,/paper[\s_-]*only/gi];

  function cleanText(value){
    let out=String(value||'');
    for(const pattern of patterns)out=out.replace(pattern,replacement);
    return out;
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
