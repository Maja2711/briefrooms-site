(function(){
  'use strict';
  if(window.__BR_LEGAL_FOOTER__) return;
  window.__BR_LEGAL_FOOTER__ = true;
  function isEn(){return (document.documentElement.lang||'').toLowerCase().indexOf('en')===0 || location.pathname.indexOf('/en/')===0;}
  function readingTheme(){
    if(location.pathname!=='/pl/nauka/ciemny-tlen.html' || document.getElementById('br-cream-reading-theme')) return;
    var s=document.createElement('style');
    s.id='br-cream-reading-theme';
    s.textContent='body.rooms-light{background:#f5eee5!important;color:#1f2937!important}body.rooms-light header h1,body.rooms-light h2,body.rooms-light h3{color:#0f172a!important}body.rooms-light .lead{color:#566170!important}body.rooms-light .pill{background:rgba(255,255,255,.86)!important;border-color:#ddd3c5!important;color:#475569!important;box-shadow:0 3px 10px rgba(71,52,32,.05)!important}body.rooms-light .card{background:#fffdf9!important;border-color:#e1d7ca!important;box-shadow:0 14px 34px -22px rgba(73,54,34,.34),inset 0 1px 0 rgba(255,255,255,.85)!important}body.rooms-light .card::after{background:radial-gradient(circle,rgba(47,111,237,.055),transparent 68%)!important}body.rooms-light .hero{background:#fffdf9!important}body.rooms-light .hero-copy{background:linear-gradient(145deg,#fffdf9,#faf5ed)!important}body.rooms-light strong{color:#0f172a!important}body.rooms-light .fact{background:#f6f1e9!important;border-color:#dfd5c8!important}body.rooms-light .fact strong{color:#162033!important}body.rooms-light .fact span,body.rooms-light .source-note,body.rooms-light .back{color:#667085!important}body.rooms-light .thesis{background:linear-gradient(145deg,#f4f8ff,#fffdf9)!important;border-left-color:#4f8fe8!important}body.rooms-light .counter{border-left-color:#d79a32!important}body.rooms-light .status{border-left-color:#2f9d8f!important}body.rooms-light .quote{background:#eef5ff!important;border-color:#c9dcf5!important;color:#26364b!important}body.rooms-light .callout{background:#edf8f5!important;border-color:#bddfd7!important;color:#244b45!important}body.rooms-light .sources a,body.rooms-light .back a{color:#245fb8!important}body.rooms-light .sources a:hover,body.rooms-light .back a:hover{color:#153f7b!important}.br-legal-footer{border-top-color:#d9cec0!important;color:#6b7280!important}.br-legal-footer a,.br-legal-footer summary{color:#405168!important}.br-legal-panel{background:#fffaf3!important;border-color:#ddd3c5!important;color:#596579!important}';
    document.head.appendChild(s);
  }
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
    readingTheme();
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