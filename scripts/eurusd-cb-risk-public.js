(function (global) {
  'use strict';
  const esc = value => String(value ?? '—').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function view(record, en, now = Date.now()) {
    const row = record?.assessment || record?.decision_record || record;
    const d = row?.diagnostics || {};
    const at = Date.parse(row?.evaluated_at || '');
    const age = (now-at)/1000;
    const fresh = Number.isFinite(age) && age >= 0 && age <= 900;
    const known = ['HOLD','PROTECT_PROFIT','CLOSE_BEFORE_EVENT','BLOCK_NEW_ENTRY','NO_ACTION_UNVERIFIED'].includes(row?.action);
    const actions = en ? {HOLD:'Hold',PROTECT_PROFIT:'Protect profit',CLOSE_BEFORE_EVENT:'Close before event',BLOCK_NEW_ENTRY:'Block new entry',NO_ACTION_UNVERIFIED:'Await verified data'} : {HOLD:'Utrzymanie',PROTECT_PROFIT:'Ochrona zysku',CLOSE_BEFORE_EVENT:'Zamknięcie przed wydarzeniem',BLOCK_NEW_ENTRY:'Blokada nowego wejścia',NO_ACTION_UNVERIFIED:'Oczekiwanie na wiarygodne dane'};
    const dash = '—';
    const num = (n, suffix) => typeof n === 'number' && Number.isFinite(n) ? `${n.toFixed(2)} ${suffix}` : dash;
    const title = en ? 'Fed / ECB · EUR/USD risk' : 'Fed / EBC · ryzyko EUR/USD';
    const status = fresh && known ? actions[row.action] : (en ? 'Assessment unavailable or stale' : 'Ocena niedostępna lub nieaktualna');
    const quote = row?.quote_verified === true && fresh;
    const source = d.latest_official_document;
    let safeUrl = '';
    try { const u = new URL(source); if(u.protocol === 'https:' && ['www.federalreserve.gov','federalreserve.gov','www.ecb.europa.eu','ecb.europa.eu'].includes(u.hostname) && !u.username && !u.password) safeUrl = u.href; } catch {}
    const exitPrice = typeof d.current_executable_exit_price === 'number' ? d.current_executable_exit_price.toFixed(5) : dash;
    return `<section class="eurusd-cb-status" aria-label="${esc(title)}"><strong>${esc(title)}</strong><p>${esc(status)} · SHADOW</p><p>${en ? 'Sources' : 'Źródła'}: ${esc(d.wire_health || 'UNAVAILABLE')} · ${en ? 'Event verified' : 'Wydarzenie potwierdzone'}: ${row?.event_verified ? (en?'yes':'tak') : (en?'no':'nie')}</p><p>${quote ? (en?'Verified exit quote':'Zweryfikowana cena wyjścia') : (en?'No verified executable quote':'Brak zweryfikowanej ceny wykonania')}: ${quote ? esc(exitPrice) : dash}</p><p>${en?'Profit at risk to SL':'Od bieżącej ceny do SL'}: ${esc(num(d.profit_at_risk_pips,'pips'))} · ${en?'Net result':'Wynik netto'}: ${esc(num(row?.current_net_R,'R'))}</p><p>${en?'As of':'Stan na'}: ${Number.isFinite(at) ? esc(new Date(at).toISOString()) : dash}</p><small>${en?'Observation only; no new close or stop change. Live speech unverified.':'Wyłącznie obserwacja; bez nowego zamknięcia i zmiany stopa. Wypowiedź na żywo niepotwierdzona.'}</small>${safeUrl ? `<p><a href="${esc(safeUrl)}" target="_blank" rel="noopener noreferrer">${en?'Official document':'Dokument źródłowy'}</a></p>` : ''}</section>`;
  }
  global.BriefRoomsEurusdCbRisk = {view};
  if (typeof document === 'undefined') return;
  const daily = document.getElementById('eurusd-daily-root');
  const weekly = document.getElementById('app');
  if (!daily && !weekly) return;
  const en = document.documentElement.lang.toLowerCase().startsWith('en');
  const style = document.createElement('style');
  style.textContent = '.eurusd-cb-status{margin:14px 0;padding:12px;border:1px solid #496074;border-radius:10px;color:#c7d4e1;font-size:12px;line-height:1.5;overflow-wrap:anywhere}.eurusd-cb-status p{margin:5px 0}.eurusd-cb-status small{color:#aebdca}.eurusd-cb-status a{color:#9ffff6}';
  document.head.append(style);
  let record = null, selectedWeek = null;
  function draw() {
    if (daily) {
      let panel = document.getElementById('eurusd-cb-daily-status');
      if (!panel) {panel=document.createElement('div');panel.id='eurusd-cb-daily-status';}
      const history=daily.querySelector('.brfx-card > details');
      if(history) history.before(panel); else daily.after(panel);
      panel.innerHTML=view(record,en);
    } else {
      const card=document.querySelector('[data-eurusd-cb-card]');
      const prior=document.getElementById('eurusd-cb-weekly-status');
      if (prior) prior.remove();
      if (!card) return;
      const panel=document.createElement('div');panel.id='eurusd-cb-weekly-status';
      const sameWeek=record?.week_id && record.week_id===(selectedWeek || card.dataset.cbWeekId);
      let sourcePosition=null;try{sourcePosition=JSON.parse(card.dataset.cbPositionRef || 'null');}catch{}
      const samePosition=sourcePosition && record?.source_position_ref && Object.keys(sourcePosition).every(key=>(record.source_position_ref[key] ?? null)===sourcePosition[key]);
      panel.innerHTML=view(sameWeek && samePosition ? record : null,en);card.append(panel);
    }
  }
  async function refresh() {
    const controller=new AbortController(); const timeout=setTimeout(()=>controller.abort(),4000);
    try {
      const path=daily?'eurusd_daily_cb_event_risk.json':'eurusd_weekly_cb_event_risk.json';
      const response=await fetch('/data/investments/'+path+'?v='+Date.now(),{cache:'no-store',signal:controller.signal});
      if(response.ok) record=await response.json();
      if(daily) {
        const canonical=await fetch('/data/investments/eurusd_daily_spot.json?v='+Date.now(),{cache:'no-store',signal:controller.signal});
        if(!canonical.ok) {record=null;return;}
        const payload=await canonical.json();
        if(!response.ok) record=payload.metadata?.central_bank_event_risk || null;
        const position=payload.metadata?.position;
        const currentId=position?.status==='OPEN' ? (position.trade_id || position.position_id || null) : null;
        if((record?.position_id || null)!==currentId) record=null;
      }
    } catch {if(daily)record=null;} finally {clearTimeout(timeout);draw();}
  }
  document.addEventListener('br:weekly-rendered',event=>{selectedWeek=event.detail?.week_id;draw();});
  document.addEventListener('br:eurusd-daily-rendered',draw);
  refresh();
  setInterval(refresh,60000);
})(globalThis);
