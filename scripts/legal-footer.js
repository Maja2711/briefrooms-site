(function(){
  'use strict';
  if(window.__BR_LEGAL_FOOTER__) return;
  window.__BR_LEGAL_FOOTER__ = true;
  function isEn(){return (document.documentElement.lang||'').toLowerCase().indexOf('en')===0 || location.pathname.indexOf('/en/')===0;}
  function style(){
    if(document.getElementById('br-legal-footer-style')) return;
    var s=document.createElement('style');
    s.id='br-legal-footer-style';
    s.textContent='.br-legal-footer{max-width:1120px;margin:44px auto 0!important;padding:18px 16px 24px!important;border-top:1px solid rgba(255,255,255,.10)!important;color:rgba(210,225,240,.62)!important;font-size:10.5px!important;line-height:1.45!important;text-align:left!important;opacity:1!important;background:transparent!important}.health-hub>.br-legal-footer{margin:auto auto 0!important;width:100%!important}.br-legal-footer a{color:rgba(220,238,255,.78)!important;text-decoration:none!important}.br-legal-footer a:hover{text-decoration:underline!important}.br-legal-main{margin:0 0 10px!important}.br-legal-links{display:flex!important;flex-wrap:wrap!important;gap:8px 14px!important}.br-legal-footer summary{cursor:pointer!important;list-style:none!important;color:rgba(225,238,250,.72)!important;font-weight:650!important}.br-legal-footer summary::-webkit-details-marker{display:none!important}.br-legal-footer summary:after{content:" +"}.br-legal-footer details[open] summary:after{content:" -"}.br-legal-panel{margin-top:8px!important;max-width:790px!important;padding:10px 12px!important;border:1px solid rgba(255,255,255,.09)!important;border-radius:10px!important;background:rgba(255,255,255,.035)!important;color:rgba(210,225,240,.66)!important}.br-legal-panel p{margin:0 0 8px!important;font-size:10.5px!important;line-height:1.45!important}@media(max-width:640px){.br-legal-links{display:block!important}.br-legal-footer details{margin-bottom:8px!important}}';
    document.head.appendChild(s);
  }
  function item(title, body){return '<details><summary>'+title+'</summary><div class="br-legal-panel">'+body+'</div></details>';}
  function render(){
    style();
    var en=isEn();
    var html;
    if(en){
      html='<p class="br-legal-main">© 2026 BriefRooms. Information service based on short, source-linked summaries. Contact: <a href="mailto:contact@briefrooms.com">contact@briefrooms.com</a></p><div class="br-legal-links">'+
      item('Privacy','<p>The operator of BriefRooms.com may process basic technical data needed to run and secure the website, analyse traffic and handle contact. This can include IP address, browser, device data, visit time and data provided by e-mail.</p><p>Users may contact BriefRooms about privacy matters at <a href="mailto:contact@briefrooms.com">contact@briefrooms.com</a>. The site may use cookies or similar technologies for technical and analytical purposes.</p>')+
      item('Terms','<p>BriefRooms provides short summaries, comments and links to external sources. The content is informational and educational. BriefRooms does not guarantee that every item is complete, current or error-free.</p><p>Users should verify important information in the original sources. BriefRooms is not responsible for external websites linked from the service.</p>')+
      item('Medical disclaimer','<p>Health and medical content is informational and educational only. It is not medical advice, diagnosis or treatment instruction. Decisions about health, medicines, supplements, diagnostics or treatment should be discussed with a qualified professional.</p>')+
      item('Financial disclaimer','<p>Financial, market and investment content is informational and educational only. It is not investment recommendation, financial advice, tax advice, legal advice or an invitation to buy or sell any instrument. Investing involves risk of capital loss.</p>')+
      '</div>';
    } else {
      html='<p class="br-legal-main">© 2026 BriefRooms. Serwis informacyjny oparty na krótkich, źródłowych podsumowaniach. Kontakt: <a href="mailto:contact@briefrooms.com">contact@briefrooms.com</a></p><div class="br-legal-links">'+
      item('Prywatność','<p>Operator BriefRooms.com może przetwarzać podstawowe dane techniczne potrzebne do działania i bezpieczeństwa strony, analizy ruchu oraz obsługi kontaktu. Może to obejmować adres IP, przeglądarkę, dane urządzenia, czas wizyty i dane podane w e-mailu.</p><p>W sprawach prywatności można pisać na <a href="mailto:contact@briefrooms.com">contact@briefrooms.com</a>. Serwis może korzystać z cookies lub podobnych technologii w celach technicznych i analitycznych.</p>')+
      item('Regulamin','<p>BriefRooms udostępnia krótkie podsumowania, komentarze i linki do źródeł zewnętrznych. Treści mają charakter informacyjny i edukacyjny. BriefRooms nie gwarantuje, że każda informacja jest kompletna, aktualna lub wolna od błędów.</p><p>Użytkownik powinien weryfikować istotne informacje w oryginalnych źródłach. BriefRooms nie odpowiada za zewnętrzne strony linkowane z serwisu.</p>')+
      item('Disclaimer medyczny','<p>Treści zdrowotne i medyczne mają wyłącznie charakter informacyjny i edukacyjny. Nie są poradą lekarską, diagnozą ani instrukcją leczenia. Decyzje dotyczące zdrowia, leków, suplementów, diagnostyki lub leczenia należy konsultować z uprawnionym specjalistą.</p>')+
      item('Disclaimer finansowy','<p>Treści finansowe, rynkowe i inwestycyjne mają wyłącznie charakter informacyjny i edukacyjny. Nie są rekomendacją inwestycyjną, poradą finansową, podatkową, prawną ani zachętą do kupna lub sprzedaży instrumentów. Inwestowanie wiąże się z ryzykiem utraty kapitału.</p>')+
      '</div>';
    }
    var f=document.querySelector('footer.br-legal-footer')||document.querySelector('footer');
    if(!f){f=document.createElement('footer');document.body.appendChild(f);}
    f.className='br-legal-footer';
    f.setAttribute('role','contentinfo');
    f.innerHTML=html;
  }
  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',render); else render();
})();
