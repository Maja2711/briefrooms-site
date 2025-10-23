/* === BriefRooms – Comments (JS) – v3 ===
   - większe pole na komentarz
   - brak "Strona: …"
   - dokładniejsze komunikaty błędów
*/

(() => {
  // 1) Endpoint: najpierw bierze z HTML-a (BR_COMMENTS_ENDPOINT), a jak nie ma – z tej stałej.
  const DEFAULT_EP = "https://script.googleusercontent.com/macros/echo?user_content_key=AehSKLgjojMVF5JPhBUzvHT2md6hP2Lx_93gbdqArN-PI65lNkdKsALfP5wpj4HhfvfOlB5A_6SCrK4TEY2xOk2ATX83u_H26tSxPO5e6vdIMAd1eBziO4NGI0MWCJESiT_hbTa3VFNxkfPBKCTNcnjWpVbV-ie0a6MMk_qAMbmTEPkQad_rwzy_h0n2TGDuEO-aFEkZIOCsRkQ8JNTqfo2vMPooO-jmm5pDLe60lGMsg6uXjfuPs7KAeTbD4OwSZaxfPfzwYrX1bUepPZKmY1ct5JVlzEcmRg&lib=MF2kJZiB0iU3yfW9Wf0agcCAggSt92OEn";
  const EP = (typeof window !== "undefined" && window.BR_COMMENTS_ENDPOINT) || DEFAULT_EP;
  const MAX_LEN = 2000;

  const esc = s => String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");

  function uid(){
    let u = localStorage.getItem("br_uid");
    if(!u){
      u = Math.random().toString(36).slice(2) + Date.now().toString(36);
      localStorage.setItem("br_uid", u);
    }
    return u;
  }
  function defaultNick(){
    const saved = localStorage.getItem("br_nick");
    if(saved) return saved;
    const n = "Gość " + Math.floor(1000 + Math.random()*9000);
    localStorage.setItem("br_nick", n);
    return n;
  }

  async function safeJson(resp){
    const txt = await resp.text();
    try { return { ok: true, json: JSON.parse(txt), raw: txt }; }
    catch { return { ok: false, json: null, raw: txt }; }
  }

  function render(root){
    const page = root.dataset.page || location.pathname;

    root.innerHTML = `
      <div class="brc">
        <h3>Komentarze</h3>

        <form class="brc-form" id="brc-form">
          <div class="brc-row">
            <input id="brc-nick" type="text" maxlength="64" value="${esc(defaultNick())}" aria-label="Nick">
            <textarea id="brc-text" maxlength="${MAX_LEN}" placeholder="Dodaj komentarz (maks. ${MAX_LEN} znaków)"></textarea>
          </div>
          <div class="brc-actions">
            <button id="brc-send" type="submit">Wyślij</button>
            <span class="brc-err" id="brc-err" hidden></span>
          </div>
        </form>

        <ul class="brc-list" id="brc-list"><li class="brc-empty">Ładowanie…</li></ul>
      </div>
      <style>
        /* większe pole komentarza + lekki tuning */
        .brc textarea#brc-text{min-height:120px}
        .brc .brc-row{display:flex; gap:12px; flex-wrap:wrap}
        .brc .brc-row input{max-width:260px}
        .brc .brc-actions{display:flex; align-items:center; gap:12px; margin-top:6px}
        .brc .brc-err{color:#f5a3a3}
      </style>
    `;

    const $ = sel => root.querySelector(sel);
    const list = $("#brc-list");
    const err = $("#brc-err");
    const nickInp = $("#brc-nick");
    const textInp = $("#brc-text");
    const form = $("#brc-form");
    const sendBtn = $("#brc-send");

    function showErr(msg){
      if(msg){ err.textContent = msg; err.hidden = false; }
      else { err.textContent = ""; err.hidden = true; }
    }

    async function load(){
      try{
        const r = await fetch(EP + "?page=" + encodeURIComponent(page), { cache:"no-store" });
        const { ok, json, raw } = await safeJson(r);

        list.innerHTML = "";
        if(!ok || !json){
          list.innerHTML = `<li class="brc-empty">Błąd odpowiedzi (GET). ${esc(raw.slice(0,150))}</li>`;
          return;
        }
        if(!json.rows || !json.rows.length){
          list.innerHTML = `<li class="brc-empty">Bądź pierwszy/a, który/a skomentuje.</li>`;
          return;
        }
        for(const it of json.rows){
          const [ts, pg, nick, text] = it;
          const dt = new Date(ts);
          const li = document.createElement("li");
          li.className = "brc-item";
          li.innerHTML = `
            <div class="brc-meta">
              <span class="brc-nick">${esc(nick || "Gość")}</span> ·
              <time datetime="${dt.toISOString()}">${dt.toLocaleString()}</time>
            </div>
            <div class="brc-text">${esc(text || "")}</div>`;
          list.appendChild(li);
        }
      }catch(e){
        list.innerHTML = `<li class="brc-empty">Nie udało się pobrać komentarzy (GET). ${esc(String(e))}</li>`;
      }
    }

    form.addEventListener("submit", async (e)=>{
      e.preventDefault();
      showErr("");
      const n = nickInp.value.trim();
      const t = textInp.value.trim();
      if(!t){ showErr("Wpisz treść komentarza."); return; }
      if(t.length > MAX_LEN){ showErr("Za długi komentarz."); return; }

      sendBtn.disabled = true;
      try{
        localStorage.setItem("br_nick", n || "Gość");
        const body = { contents: { page, nick: n || "Gość", text: t, uid: uid() } };

        // UWAGA: 'text/plain' = prosty request (bez preflight). Apps Script to rozumie.
        const r = await fetch(EP, {
          method: "POST",
          mode: "cors",
          headers: { "Content-Type": "text/plain;charset=utf-8" },
          body: JSON.stringify(body)
        });

        const { ok, json, raw } = await safeJson(r);

        if(!ok || !json){
          throw new Error("Zła odpowiedź (POST): " + raw.slice(0,180));
        }
        if(json.ok !== true){
          throw new Error("API zwróciło błąd: " + (json.error || "nieznany"));
        }

        textInp.value = "";
        await load();
      }catch(e2){
        showErr("Wysyłka nie powiodła się: " + String(e2).replace(/^Error:\s*/,''));
      }finally{
        sendBtn.disabled = false;
      }
    });

    load();
  }

  const mount = () => {
    document.querySelectorAll("#br-comments").forEach(render);
  };
  if(document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount);
  else mount();
})();
