#!/usr/bin/env python3
from __future__ import annotations
import html,json,math
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
WD=ROOT/'data/investments/weekly'; LIVE=ROOT/'data/investments/live_prices.json'
PL=ROOT/'pl/inwestycje/prognozy-tygodniowe.html'; EN=ROOT/'en/investing/weekly-forecasts.html'

def f(x):
    try:
        if x is None: return None
        v=float(x); return v if math.isfinite(v) else None
    except Exception: return None

def load(p,d):
    try: return json.loads(p.read_text(encoding='utf-8'))
    except Exception: return d

def weeks(): return [load(p,{}) for p in sorted(WD.glob('*.json'))]
def dirn(i):
    d=str(i.get('direction') or i.get('effective_direction') or '')
    if d in ('long','short'): return d
    s=f(i.get('score')); return 'short' if s is not None and s<0 else 'long'
def price(x,inst):
    v=f(x)
    if v is None: return '—'
    return f'{v:.5f}' if inst=='eurusd' else f'{v:,.2f}'.replace(',',' ')
def value(i,mark,stored=False):
    if stored and f(i.get('result_value')) is not None: return f(i.get('result_value'))
    e=f(i.get('entry_price'))
    if e is None or mark is None or e==0: return None
    mv=(mark-e) if dirn(i)=='long' else (e-mark)
    if i.get('instrument_id')=='eurusd': return mv*(f(i.get('notional_eur')) or 10000)
    return mv/e*(f(i.get('notional_usd')) or 10000)
def units(i,mark):
    e=f(i.get('entry_price'))
    if e is None or mark is None: return None
    mv=(mark-e) if dirn(i)=='long' else (e-mark)
    return mv/0.0001 if i.get('instrument_id')=='eurusd' else mv
def res(i,mark,lang,stored=False):
    if mark is None: return ('w trakcie' if lang=='pl' else 'open','neutral')
    v=value(i,mark,stored); u=units(i,mark); inst=str(i.get('instrument_id') or '')
    tone='positive' if (v or 0)>0 else 'negative' if (v or 0)<0 else 'neutral'
    unit='pipsów' if inst=='eurusd' and lang=='pl' else 'pips' if inst=='eurusd' else 'pkt' if lang=='pl' else 'pts'
    out=[]
    if v is not None: out.append(f'{v:+.2f} USD')
    if u is not None: out.append(f'{u:+.1f} {unit}')
    return (' · '.join(out) if out else '—',tone)
def live_price(i,live):
    k=str(i.get('instrument_id') or '')
    return f((live.get(k) or {}).get('price')) or f(i.get('exit_observed_price')) or f(i.get('exit_price')) or f(i.get('entry_price'))
def m(label,val,tone=''):
    return f"<div class='m {tone}'><small>{html.escape(label)}</small><b>{html.escape(val)}</b></div>"
def card(i,lang,live):
    inst=str(i.get('instrument_id') or ''); name=str(i.get('label_pl' if lang=='pl' else 'label_en') or i.get('symbol') or inst)
    d=dirn(i); now=live_price(i,live); close=f(i.get('exit_price')); plan=i.get('risk_plan') if isinstance(i.get('risk_plan'),dict) else {}
    live_txt,live_t=res(i,now,lang,False); final_txt,final_t=res(i,close,lang,True) if close is not None else (('Tydzień trwa' if lang=='pl' else 'Week in progress'),'neutral')
    close_txt=price(close,inst) if close is not None else ('Tydzień trwa' if lang=='pl' else 'Week in progress')
    status=str(i.get('exit_reason') or ('Tydzień trwa' if lang=='pl' else 'Week in progress'))
    notional='10 000 EUR' if inst=='eurusd' and lang=='pl' else '10,000 EUR' if inst=='eurusd' else '10 000 USD' if lang=='pl' else '10,000 USD'
    labels=('Cena otwarcia','Cena zamknięcia','Zysk / strata teraz','Wynik po zamknięciu','Nominał','Status') if lang=='pl' else ('Open','Close','Profit / loss now','Final result','Notional','Status')
    body=m(labels[0],price(i.get('entry_price'),inst))+m(labels[1],close_txt)+m('Stop loss',price(plan.get('stop_loss_price'),inst))+m('Take profit',price(plan.get('take_profit_price'),inst))+m(labels[2],live_txt,live_t)+m(labels[3],final_txt,final_t)+m(labels[4],notional)+m(labels[5],status)
    return f"<article class='card {d}'><div class='ch'><div><small>{'Pozycja edukacyjna' if lang=='pl' else 'Educational position'}</small><h3>{html.escape(name)}</h3></div><em>{d.upper()}</em></div><div class='now'><span>{'Cena teraz' if lang=='pl' else 'Price now'}</span><strong>{price(now,inst)}</strong></div><div class='mg'>{body}</div></article>"
def render(lang):
    ws=weeks(); latest=ws[-1] if ws else {}; items=latest.get('instruments',[]) if isinstance(latest,dict) else []
    live=load(LIVE,{}).get('prices',{}); pairs=[(w,i) for w in ws for i in (w.get('instruments',[]) if isinstance(w,dict) else [])]
    vals=[]
    for w,i in pairs:
        c=f(i.get('exit_price')); v=value(i,c,True) if c is not None else None
        if v is not None: vals.append(v)
    total=sum(vals); wins=sum(1 for v in vals if v>0); losses=sum(1 for v in vals if v<0); tone='positive' if total>0 else 'negative' if total<0 else 'neutral'
    if lang=='pl':
        title='Otwarte pozycje tygodniowe'; lead='Cena teraz jest aktualizowana z live_prices. Historia i suma obejmują wszystkie zamknięte pozycje.'; nav='<a href="/pl/inwestycje.html">Inwestycje</a><a class="active" href="/pl/inwestycje/prognozy-tygodniowe.html">Pozycje tygodniowe</a><a href="/pl/inwestycje/spx-scenariusze-2026.html">Scenariusze S&P 500</a>'; home='/pl/'; sw='/en/investing/weekly-forecasts.html'; sl='EN'; total_lab='Łączny wynik wszystkich zamkniętych pozycji'; wl='Zyskowne / stratne pozycje'; hist='Historia wszystkich pozycji'; openword='w trakcie'; back='← Wróć do pokoju Inwestycje'; backurl='/pl/inwestycje.html'
        head='<tr><th>Tydzień</th><th>Instrument</th><th>Pozycja</th><th>Otwarcie</th><th>Zamknięcie</th><th>Powód</th><th>Wynik</th></tr>'
    else:
        title='Open weekly positions'; lead='Price now is updated from live_prices. History and totals include all closed positions.'; nav='<a href="/en/investing.html">Investing</a><a class="active" href="/en/investing/weekly-forecasts.html">Weekly positions</a><a href="/en/investing/spx-scenarios-2026.html">S&P 500 scenarios</a>'; home='/en/'; sw='/pl/inwestycje/prognozy-tygodniowe.html'; sl='PL'; total_lab='Total result of all closed positions'; wl='Profitable / losing positions'; hist='Full position history'; openword='open'; back='← Back to Investing room'; backurl='/en/investing.html'
        head='<tr><th>Week</th><th>Instrument</th><th>Position</th><th>Open</th><th>Close</th><th>Reason</th><th>Result</th></tr>'
    rows=[]
    for w,i in reversed(pairs):
        inst=str(i.get('instrument_id') or ''); name=str(i.get('label_pl' if lang=='pl' else 'label_en') or i.get('symbol') or inst); c=f(i.get('exit_price')); rt,tn=res(i,c,lang,True) if c is not None else (openword,'neutral')
        rows.append(f"<tr><td>{html.escape(str(w.get('week_id') or ''))}</td><td>{html.escape(name)}</td><td>{dirn(i).upper()}</td><td>{price(i.get('entry_price'),inst)}</td><td>{price(c,inst) if c is not None else openword}</td><td>{html.escape(str(i.get('exit_reason') or openword))}</td><td class='{tn}'>{html.escape(rt)}</td></tr>")
    css='body{margin:0;background:#050b12;color:#eef7ff;font-family:Inter,system-ui,Segoe UI,Arial,sans-serif}body:before{content:"";position:fixed;inset:0;background:linear-gradient(120deg,transparent 0 16%,rgba(56,214,201,.14) 16.2%,transparent 16.8% 45%,rgba(255,191,63,.12) 45.2%,transparent 45.8%),linear-gradient(rgba(255,255,255,.025) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.02) 1px,transparent 1px);background-size:100% 100%,44px 44px,44px 44px;pointer-events:none}a{color:inherit;text-decoration:none}.wrap{max-width:1180px;margin:auto;padding:24px;position:relative}header{display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid rgba(255,255,255,.13);padding-bottom:18px}.logo{font-weight:950;font-size:26px}.pill,.nav a{border:1px solid rgba(255,255,255,.12);border-radius:999px;padding:8px 12px;background:rgba(255,255,255,.05)}.nav{display:flex;gap:12px}.nav .active{background:linear-gradient(135deg,#38d6c9,#52e38b);color:#062026;font-weight:900}h1{font-size:clamp(42px,7vw,76px);line-height:1;margin:42px 0 12px}.lead{color:#bfd0e0;font-size:20px}.panel,.card{border:1px solid rgba(255,255,255,.12);background:linear-gradient(180deg,rgba(255,255,255,.10),rgba(255,255,255,.04));border-radius:24px;padding:22px;margin:20px 0;box-shadow:0 22px 58px rgba(0,0,0,.25)}.summary{display:grid;grid-template-columns:1fr 260px;gap:18px}.kpi{background:rgba(3,10,18,.5);border-radius:18px;padding:18px}.kpi span,.m small,.ch small{color:#8fa4b8;text-transform:uppercase;font-size:12px;font-weight:900}.kpi strong{display:block;font-size:32px;margin-top:8px}.cards{display:grid;grid-template-columns:repeat(3,1fr);gap:18px}.card.short{border-top:6px solid #ff4d6d}.card.long{border-top:6px solid #52e38b}.ch{display:flex;justify-content:space-between;gap:10px}.ch h3{margin:5px 0 0}.ch em{font-style:normal;font-weight:950}.short em,.negative{color:#ff4d6d}.long em,.positive{color:#52e38b}.neutral{color:#ffbf3f}.now{background:rgba(255,255,255,.05);border-radius:16px;padding:14px;margin:14px 0}.now span{color:#8fa4b8}.now strong{display:block;font-size:30px}.mg{display:grid;grid-template-columns:1fr 1fr;gap:10px}.m{background:rgba(255,255,255,.04);border-radius:14px;padding:12px}.m b{display:block;margin-top:6px;overflow-wrap:anywhere}details{border:1px solid rgba(255,255,255,.12);border-radius:18px;padding:14px;background:rgba(255,255,255,.04)}summary{cursor:pointer;font-weight:900;color:#9ffff6}table{width:100%;border-collapse:collapse;min-width:760px}th,td{border-bottom:1px solid rgba(255,255,255,.1);padding:10px;text-align:left}.scroll{overflow:auto;margin-top:12px}.back{display:inline-block;margin:24px 0;color:#9ffff6}@media(max-width:980px){.nav{display:none}.summary,.cards,.mg{grid-template-columns:1fr}}'
    return f"<!doctype html><html lang='{lang}'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{title} | BriefRooms</title><link rel='icon' href='/assets/favicon.svg'><style>{css}</style></head><body><div class='wrap'><header><a href='{home}' class='logo'>BRIEFROOMS</a><nav class='nav'>{nav}</nav><a class='pill' href='{sw}'>{sl}</a></header><h1>{title}</h1><p class='lead'>{lead}</p><section class='panel'><h2>{total_lab}</h2><div class='summary'><div class='kpi'><span>{total_lab}</span><strong class='{tone}'>{total:+.2f} USD</strong></div><div class='kpi'><span>{wl}</span><strong>{wins} / {losses}</strong></div></div></section><section class='cards'>{''.join(card(i,lang,live) for i in items)}</section><section class='panel'><details open><summary>{hist}</summary><div class='scroll'><table>{head}<tbody>{''.join(rows)}</tbody></table></div></details></section><a class='back' href='{backurl}'>{back}</a></div></body></html>\n"

def main():
    PL.parent.mkdir(parents=True,exist_ok=True); EN.parent.mkdir(parents=True,exist_ok=True)
    PL.write_text(render('pl'),encoding='utf-8'); EN.write_text(render('en'),encoding='utf-8')
    print('rendered public weekly positions with full history')
if __name__=='__main__': main()
