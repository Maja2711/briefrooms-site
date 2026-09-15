(()=>{
  'use strict';

  const replacements=new Map([
    ['Warstwa ciągłej ekspozycji pozostaje wyłącznie eksperymentem paper-trading.','Warstwa ciągłej ekspozycji jest częścią BriefRooms Trading Engine.'],
    ['The continuous-exposure layer remains an experimental paper-trading exercise only.','The continuous-exposure layer is part of BriefRooms Trading Engine.']
  ]);

  function sanitize(root){
    if(!root)return;
    const walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);
    let node;
    while((node=walker.nextNode())){
      let value=node.nodeValue||'';
      let next=value;
      for(const [from,to] of replacements)next=next.split(from).join(to);
      if(next!==value)node.nodeValue=next;
    }
  }

  const root=document.getElementById('app');
  if(!root)return;
  sanitize(root);
  if(typeof MutationObserver==='function'){
    const observer=new MutationObserver(records=>{
      for(const record of records){
        for(const node of record.addedNodes){
          if(node.nodeType===Node.TEXT_NODE){
            let next=node.nodeValue||'';
            for(const [from,to] of replacements)next=next.split(from).join(to);
            if(next!==node.nodeValue)node.nodeValue=next;
          }else if(node.nodeType===Node.ELEMENT_NODE){
            sanitize(node);
          }
        }
      }
    });
    observer.observe(root,{childList:true,subtree:true});
    window.addEventListener('pagehide',()=>observer.disconnect(),{once:true});
  }
})();
