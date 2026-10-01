(()=>{"use strict";
if(!String(document.documentElement.lang||"").toLowerCase().startsWith("en"))return;

const replacements=[
["S&P 500 będzie wyżej w momencie Target niż w chwili prognozy","S&P 500 will be higher at Target than at forecast time"],
["Breadth rynku USA pozostaje zdrowy","US market breadth remains healthy"],
["Zmienność rynku pozostaje umiarkowana","Market volatility remains moderate"],
["Płynność / kredyt wspierają rynek","Liquidity / credit support the market"],
["Warunki finansowe wspierają rynek","Financial conditions support the market"],
["EUR/USD będzie wyżej w momencie Target niż w chwili prognozy","EUR/USD will be higher at Target than at forecast time"],
["Presja stóp USA wspiera EUR/USD","US rates pressure supports EUR/USD"],
["Otoczenie USD wspiera EUR/USD","USD environment supports EUR/USD"],
["BTC/USD będzie wyżej w momencie Target niż w chwili prognozy","BTC/USD will be higher at Target than at forecast time"],
["Zmienność BTC pozostaje umiarkowana","BTC volatility remains moderate"],
["Płynność wspiera BTC","Liquidity supports BTC"],
["Otoczenie USD wspiera BTC","USD environment supports BTC"],
["Brak opublikowanych forecastów. Model nie tworzy danych demonstracyjnych.","No published forecasts. The model does not generate demonstration data."],
["Brak aktualnego Market View.","No current Market View."],
["Brak danych do krzywej kalibracji.","No data available for the calibration curve."],
["Brak rejestru v3 Candidate.","No v3 Candidate registry available."],
["Brak patternów spełniających obecne kryteria.","No patterns meet the current criteria."],
["Pattern Discovery publikuje tylko wzorce z dodatnim MDL gain odkryte na danych frozen-before-outcome. Nie generujemy danych demonstracyjnych.","Pattern Discovery publishes only patterns with positive MDL gain discovered on frozen-before-outcome data. We do not generate demonstration data."],
["Wybierz pattern","Select a pattern"],
["Kliknij rekord w tabeli, aby zobaczyć discovery, holdout, regimes, residual i możliwy modifier.","Click a table row to view discovery, holdout, regimes, residual and a possible modifier."],
["Model opłaca się dopiero po koszcie jego opisu.","A model is useful only after accounting for the cost of its description."],
["Wyjątki pozostają jawne; system ich nie usuwa.","Exceptions remain explicit; the system does not remove them."],
["Brak wystarczającego rozbicia.","Insufficient breakdown."],
["Brak stabilnego modifiera w obecnej próbce.","No stable modifier in the current sample."],
["Pattern jest powtarzalną relacją statystyczną. Nie jest dowodem związku przyczynowego i nie ma authority do zmiany decyzji lub wykonania transakcji.","The pattern is a repeatable statistical relationship. It is not evidence of causality and has no authority to change decisions or execute trades."],
["Ścieżka 3H / 12H / 24H / 3D / 5D dotyczy nowych forecastów utworzonych po uruchomieniu badania. Ten starszy rekord nie jest przepisywany wstecznie.","The 3H / 12H / 24H / 3D / 5D path applies to new forecasts created after the research launch. This older record is not rewritten retroactively."],
["Rekord sprzed Forecast Contract v2 — nie uzupełniamy T0/T1 wstecznie.","Record predating Forecast Contract v2 — T0/T1 are not backfilled retroactively."],
["brak automatycznej promocji i brak prawa zapisu do produkcji","no automatic promotion and no authority to write to production"],
["Agregat horyzontów pojawi się po min.","Horizon aggregate will appear after at least"],
["rozliczonych obserwacjach na horyzont.","resolved observations per horizon."],
["Rolling metrics pojawią się po","Rolling metrics will appear after"],
["rozliczonych forecastach.","resolved forecasts."],
["kalibracja → incremental information → ręczna decyzja","calibration → incremental information → manual decision"],
["pełny pipeline badawczy zaliczony","full research pipeline passed"],
["zbieranie wymaganej próby prospective","collecting the required prospective sample"],
["wymagana stabilność między reżimami","stability across regimes required"],
["wymagany niezależny frozen holdout","independent frozen holdout required"],
["odrzucony na discovery — kryteria jakości niezaliczone","rejected at discovery — quality criteria not met"],
["za mało danych, aby zaliczyć discovery","insufficient data to pass discovery"],
["Izolowany silnik generowania i walidacji kandydatów strategii. Centralny LAB tylko odczytuje jego kanoniczny raport.","An isolated engine for generating and validating strategy candidates. Central LAB only reads its canonical report."],
["Lejek pokazuje wyłącznie bramki potwierdzone przez status w kanonicznym raporcie. Brak statusu pass nie jest interpretowany jako zaliczenie.","The funnel shows only gates confirmed by status in the canonical report. Missing pass status is not interpreted as a pass."],
["TOP kandydatów najbliżej kolejnej bramki","Top candidates closest to the next gate"],
["Pipeline pozostaje niezależny: candidate → walk-forward → frozen holdout → regime → prospective shadow. Brak automatycznej promocji do produkcji.","The pipeline remains independent: candidate → walk-forward → frozen holdout → regime → prospective shadow. There is no automatic promotion to production."],
["Strategy Research jest chwilowo niedostępny.","Strategy Research is temporarily unavailable."],
["Frozen G6 i Adaptive Research pozostają jednym odseparowanym pipeline’em S&P 500. Tutaj widzisz jego stan bez przenoszenia backendu.","Frozen G6 and Adaptive Research remain one isolated S&P 500 pipeline. This view shows its state without moving the backend."],
["Pełny widok BRACE-SPX","Full BRACE-SPX view"],
["BRACE-SPX jest chwilowo niedostępny.","BRACE-SPX is temporarily unavailable."],
["Otwórz w Geopolityce","Open in Geopolitics"],
["Otwórz w Daily Trading","Open in Daily Trading"],
["Jedno miejsce nadzoru. GSE v2 oraz EUR/USD A/B/C i X pozostają w swoich domenach; Registry pokazuje tylko ich kanoniczny status.","One oversight view. GSE v2 and EUR/USD A/B/C and X remain in their own domains; the Registry only shows their canonical status."],
["Wspólna pamięć evidence z prospektywnych decyzji i późniejszych wyników. Tylko odczyt — bez writebacku, strojenia i wpływu na produkcję.","Shared evidence memory from prospective decisions and later outcomes. Read-only — no writeback, tuning or production impact."],
["Dla EUR/USD A/B/C centralny LAB wykorzystuje pełne prospektywne virtual trades i koszt 2 pips round-trip. EURUSD X jest czytany bezpośrednio z własnej projekcji shadow i pokazuje metryki aktualnego Championa po tym samym koszcie 2 pips. Żaden z tych odczytów nie ma writebacku ani prawa zmiany produkcji.","For EUR/USD A/B/C, central LAB uses full prospective virtual trades and a 2-pip round-trip cost. EURUSD X is read directly from its own shadow projection and shows metrics for the current Champion using the same 2-pip cost. None of these reads has writeback or authority to change production."],
["Registry / Experience Store są chwilowo niedostępne.","Registry / Experience Store are temporarily unavailable."],
["Sześć źródeł → zamrożona hipoteza → wyłącznie forward evidence → jedna formalna ocena fixed-N → LESSON. Zero production authority.","Six sources → frozen hypothesis → forward evidence only → one formal fixed-N evaluation → LESSON. Zero production authority."],
["Aktywne i zakończone hipotezy","Active and completed hypotheses"],
["Brak zakończonych LESSONS — HSE2 zbiera dopiero evidence po granicy T0.","No completed LESSONS — HSE2 is still collecting evidence after the T0 boundary."],
["Wynik formalny może być tylko SUPPORTED, REJECTED albo INCONCLUSIVE. HSE2 nie może zmieniać konfiguracji źródłowych silników ani produkcji.","The formal result can only be SUPPORTED, REJECTED or INCONCLUSIVE. HSE2 cannot modify source-engine configuration or production."],
["Automatyczny nadzór nad logicznymi silnikami działającymi w trybie shadow. Status pochodzi z ostatniego GitHub Actions run, a liczba obserwacji oraz Champion/Challenger z kanonicznych stanów danego silnika.","Automated oversight of logical engines running in shadow mode. Status comes from the latest GitHub Actions run, while observation counts and Champion/Challenger come from each engine's canonical state."],
["Sekcja jest wyłącznie obserwatorium. Nie ma prawa do promocji, writebacku, zmiany parametrów ani wykonywania transakcji.","This section is an observatory only. It has no authority to promote, write back, change parameters or execute trades."],
["Shadow Engines Observatory jest chwilowo niedostępny.","Shadow Engines Observatory is temporarily unavailable."],
["Niewpięte workflow shadow:","Unmapped shadow workflows:"],
["Jakość tej hipotezy","Quality of this hypothesis"],
["brak rozliczonej próby","no resolved sample"],
["trafność kierunku:","direction accuracy:"],
["mała próba","small sample"],
["P zamrożone:","Frozen P:"],
["Hipoteza potwierdzona","Hypothesis confirmed"],
["Hipoteza niepotwierdzona","Hypothesis not confirmed"],
["bardziej TAK","leans YES"],
["bardziej NIE","leans NO"],
["brak targetu","no target"],
["brak kalendarza","no calendar"],
["Rynek działa 24/7.","Market operates 24/7."],
["FX zamknięty","FX closed"],
["Settlement: pierwsze dostępne notowanie po ponownym otwarciu rynku FX.","Settlement: first available quote after the FX market reopens."],
["FX otwarty","FX open"],
["Target przypada w czasie handlu FX.","Target falls during FX trading hours."],
["rynek USA zamknięty","US market closed"],
["Settlement: pierwsze dostępne notowanie po ponownym otwarciu rynku USA.","Settlement: first available quote after the US market reopens."],
["kalendarz USA","US calendar"],
["Settlement wykorzystuje pierwsze dostępne notowanie po Target; święta i brak danych pozostają fail-closed.","Settlement uses the first available quote after Target; holidays and missing data remain fail-closed."],
["kalendarz n/d","calendar n/a"],
["WZROSTOWY","BULLISH"],
["SPADKOWY","BEARISH"],
["NEUTRALNY","NEUTRAL"],
["POZYTYWNE","POSITIVE"],
["NEGATYWNE","NEGATIVE"],
["NEUTRALNE","NEUTRAL"],
["P rośnie ↑","P rising ↑"],
["P spada ↓","P falling ↓"],
["P stabilne →","P stable →"],
["jakość badana","quality under study"],
["sygnały otoczenia","environment signals"],
["oczekuje","pending"],
["Market View przeliczono: ","Market View calculated: "],
["forecast bazowy: ","source forecast: "],
["stan ","as of "],
["Data prognozy","Forecast date"],
["Hipoteza","Hypothesis"],
["Wynik","Outcome"],
["Forecasty","Forecasts"],
["Rozliczone","Resolved"],
["Oczekujące","Pending"],
["Silniki","Engines"],
["Źródła","Sources"],
["Doświadczenia","Experiences"],
["Próba evidence","Evidence sample"],
["Evidence ogółem","Overall evidence"],
["Evidence per silnik","Evidence by engine"],
["Ostatnie doświadczenia","Recent experiences"],
["Eksperyment","Experiment"],
["Typ","Type"],
["Próba","Sample"],
["Domena","Domain"],
["Silnik","Engine"],
["Dośw.","Exp."],
["Rozl.","Resolved"],
["próg","threshold"],
["Śr. R","Avg. R"],
["Pomiar net","Net measure"],
["Ocena","Assessment"],
["Akcja","Action"],
["Zwrot","Return"],
["Wyjście","Exit"],
["Źródło","Source"],
["Werdykt","Verdict"],
["Ostatnie LESSONS","Recent LESSONS"],
["Ostatni run","Latest run"],
["Obserwacje","Observations"],
["obserwacje","observations"],
["Kandydat","Candidate"],
["Następna bramka","Next gate"],
["Dlaczego nie przeszedł","Why it did not pass"],
["Cykl research","Research cycle"],
["Kandydaci","Candidates"],
["Lejek walidacji","Validation funnel"],
["Automatic promotion: OFF.","Automatic promotion: OFF."],
["TAK","YES"],["NIE","NO"],["ROZLICZONA","RESOLVED"],["OTWARTA","OPEN"],
["Otwórz","Open"],["wygenerowano","generated"]
];

const hrefMap={
"/pl/inwestycje/brace-spx-lab.html":"/en/investing/brace-spx-lab.html",
"/pl/geopolityka.html":"/en/geopolitics.html",
"/pl/inwestycje/daily-trading.html":"/en/investing/daily-trading.html",
"/pl/inwestycje/stock-trading.html":"/en/investing/stock-trading.html",
"/pl/inwestycje/long-view.html":"/en/investing/long-view.html",
"/pl/inwestycje/decision-lab.html":"/en/investing/decision-lab.html",
"/pl/inwestycje/portfel-10k.html":"/en/investing/portfolio-10k.html",
"/pl/inwestycje/pozycje-tygodniowe.html":"/en/investing/open-weekly-positions.html",
"/pl/inwestycje/prognozy-tygodniowe.html":"/en/investing/weekly-forecasts.html",
"/pl/inwestycje/spx-scenariusze-2026.html":"/en/investing/spx-scenarios-2026.html"
};

function translateString(s){
  let out=s;
  for(const [a,b] of replacements) if(out.includes(a)) out=out.split(a).join(b);
  return out;
}
function processNode(root){
  if(!root)return;
  if(root.nodeType===Node.TEXT_NODE){
    const p=root.parentElement;
    if(!p||/^(SCRIPT|STYLE|NOSCRIPT)$/.test(p.tagName))return;
    const n=translateString(root.nodeValue||"");
    if(n!==root.nodeValue)root.nodeValue=n;
    return;
  }
  if(root.nodeType!==Node.ELEMENT_NODE&&root.nodeType!==Node.DOCUMENT_FRAGMENT_NODE)return;
  const el=root.nodeType===Node.ELEMENT_NODE?root:null;
  if(el){
    for(const attr of ["title","aria-label"]){
      if(el.hasAttribute&&el.hasAttribute(attr)){
        const old=el.getAttribute(attr),n=translateString(old);
        if(n!==old)el.setAttribute(attr,n);
      }
    }
    if(el.tagName==="A"){
      const href=el.getAttribute("href");
      if(hrefMap[href])el.setAttribute("href",hrefMap[href]);
    }
  }
  const walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);
  const nodes=[];while(walker.nextNode())nodes.push(walker.currentNode);
  nodes.forEach(processNode);
  if(root.querySelectorAll){
    root.querySelectorAll("[title],[aria-label]").forEach(processNode);
    root.querySelectorAll("a[href]").forEach(a=>{const h=a.getAttribute("href");if(hrefMap[h])a.setAttribute("href",hrefMap[h]);});
  }
}
function run(){processNode(document.querySelector(".decision-shell")||document.body);}
document.readyState==="loading"?document.addEventListener("DOMContentLoaded",run,{once:true}):run();
new MutationObserver(ms=>{for(const m of ms){if(m.type==="characterData")processNode(m.target);for(const n of m.addedNodes)processNode(n);}}).observe(document.documentElement,{subtree:true,childList:true,characterData:true});
})();