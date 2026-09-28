(()=>{"use strict";
function activate(name,focus=false){
  const tabs=[...document.querySelectorAll(".lab-glass-tabs [data-tab]")];
  const panels=[...document.querySelectorAll(".tab-panel")];
  if(!tabs.length||!panels.length)return;
  const btn=tabs.find(x=>x.dataset.tab===name);
  const panel=document.getElementById("tab-"+name);
  if(!btn||!panel)return;
  tabs.forEach(x=>{
    const on=x===btn;
    x.classList.toggle("active",on);
    x.setAttribute("aria-selected",on?"true":"false");
    x.setAttribute("tabindex",on?"0":"-1");
  });
  panels.forEach(x=>{
    const on=x===panel;
    x.classList.toggle("active",on);
    x.hidden=!on;
  });
  if(focus)btn.focus({preventScroll:true});
}
function init(){
  const tablist=document.querySelector(".lab-glass-tabs");
  if(!tablist)return;
  const tabs=[...tablist.querySelectorAll("[data-tab]")];
  const panels=[...document.querySelectorAll(".tab-panel")];
  tabs.forEach(btn=>{
    btn.type="button";
    btn.setAttribute("role","tab");
    btn.setAttribute("aria-controls","tab-"+btn.dataset.tab);
  });
  panels.forEach(panel=>{
    panel.setAttribute("role","tabpanel");
    panel.hidden=!panel.classList.contains("active");
  });
  tablist.setAttribute("role","tablist");

  tablist.addEventListener("click",e=>{
    const btn=e.target.closest("[data-tab]");
    if(!btn||!tablist.contains(btn))return;
    e.preventDefault();
    activate(btn.dataset.tab);
  });

  tablist.addEventListener("keydown",e=>{
    if(!["ArrowRight","ArrowLeft","Home","End"].includes(e.key))return;
    const current=e.target.closest("[data-tab]");
    if(!current)return;
    e.preventDefault();
    let i=tabs.indexOf(current);
    if(e.key==="ArrowRight")i=(i+1)%tabs.length;
    if(e.key==="ArrowLeft")i=(i-1+tabs.length)%tabs.length;
    if(e.key==="Home")i=0;
    if(e.key==="End")i=tabs.length-1;
    activate(tabs[i].dataset.tab,true);
  });

  const active=tabs.find(x=>x.classList.contains("active"))||tabs[0];
  if(active)activate(active.dataset.tab);
}
document.readyState==="loading"?document.addEventListener("DOMContentLoaded",init,{once:true}):init();
})();