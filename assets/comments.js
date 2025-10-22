/* === BriefRooms – Comments (JS) === */

/* 1) Endpoint
 *  - jeśli w HTML ustawisz window.BR_COMMENTS_ENDPOINT, to on wygrywa
 *  - w innym wypadku użyje stałej poniżej
 */
const BR_COMMENTS_API =
  "https://script.googleusercontent.com/macros/echo?user_content_key=AehSKLgjojMVF5JPhBUzvHT2md6hP2Lx_93gbdqArN-PI65lNkdKsALfP5wpj4HhfvfOlB5A_6SCrK4TEY2xOk2ATX83u_H26tSxPO5e6vdIMAd1eBziO4NGI0MWCJESiT_hbTa3VFNxkfPBKCTNcnjWpVbV-ie0a6MMk_qAMbmTEPkQad_rwzy_h0n2TGDuEO-aFEkZIOCsRkQ8JNTqfo2vMPooO-jmm5pDLe60lGMsg6uXjfuPs7KAeTbD4OwSZaxfPfzwYrX1bUepPZKmY1ct5JVlzEcmRg&lib=MF2kJZiB0iU3yfW9Wf0agcCAggSt92OEn";

(() => {
  const EP = window.BR_COMMENTS_ENDPOINT || BR_COMMENTS_API;
  const MAX_LEN = 2000;

  const esc = s => String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");

  function uid(){
    let u = localStorage.getItem("br_uid");
    if(!u){
      u = Math.random().toString(36).slice(2)+Date.now().toString(36);
      localStorage.setItem("br_uid",u);
    }
    return u;
  }
  function defaultNick(){
    const saved = localStorage.getItem("br_nick");
    if(saved) return saved;
    const n = "Gość " + Math.floor(1000+Math.random()*9000);
    localStorage.setItem("br_nick", n);
    return n;
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
          <div>
            <button id="brc-send" type="submit">Wyślij</button>
            <div class="brc-err" id="brc-err" hidden></div>
          </div>
        </form>
        <ul class="brc-list" id="brc-list"><li class="brc-empty">Ładowanie…</li></ul>
        <small>Strona: <code>${esc(page)}</code></small>
      </div>
    `;

    const $ = sel => root.querySelector(sel);
    const list = $("#brc-list");
    const err = $("#brc-err");
    const nickInp = $("#brc-nick");
    const textInp = $("#brc-text");
    const form = $("#brc-form");
    const sendBtn = $("#brc-send");

    const showErr = (msg) => { err.textContent = msg; err.hidden = !msg; };

    async function load(){
      try{
        const r = await fetch(EP + "?page=" + encodeURIComponent(page), { cache:"no-store" });
        const text = await r.text();
        let j;
        try { j = JSON.parse(text); } catch { j = null; }
        list.innerHTML = "";
        if(!r.ok || !j || !j.rows){
          list.innerHTML = `<li class="brc-empty">Nie udało się pobrać komentarzy.</li>`;
          if(!r.ok) console.error("GET error", r.status, text);
          return;
        }
        if(!j.rows.length){
          list.innerHTML = `<li class="brc-empty">Bądź pierwszy/a, który/a skomentuje.</li>`;
          return;
        }
        for(const it of j.rows){
          const [ts,pg,nick,text] = it;
          const dt = new Date(ts);
          const li = document.createElement("li");
          li.className = "brc-item";
          li.innerHTML = `
            <div class="brc-meta">
              <span class="brc-nick">${esc(nick||"Gość")}</span> ·
              <time datetime="${dt.toISOString()}">${dt.toLocaleString()}</time>
            </div>
            <div class="brc-text">${esc(text||"")}</div>`;
          list.appendChild(li);
        }
      }catch(e){
        console.error("GET exception", e);
        list.innerHTML = `<li class="brc-empty">Nie udało się pobrać komentarzy.</li>`;
      }
    }

    form.addEventListener("submit", async (e)=>{
      e.preventDefault(); showErr("");
      const n = nickInp.value.trim();
      const t = textInp.value.trim();
      if(!t) return showErr("Wpisz treść komentarza.");
      if(t.length > MAX_LEN) return showErr("Za długi komentarz.");
      sendBtn.disabled = true;
      try{
        localStorage.setItem("br_nick", n || "Gość");
        const body = { contents: { page, nick:n||"Gość", text:t, uid:uid() } };
        const r = await fetch(EP, {
          method:"POST",
          headers:{ "Content-Type":"text/plain;charset=utf-8" }, // simple request → brak preflight
          body: JSON.stringify(body)
        });
        const respText = await r.text();
        let j = null; try { j = JSON.parse(respText); } catch {}

        if(!r.ok || !j || j.ok !== true){
          console.error("POST error", r.status, respText);
          showErr("Wysyłka nie powiodła się. Spróbuj ponownie.");
          return;
        }
        textInp.value = "";
        await load();
      }catch(e){
        console.error("POST exception", e);
        showErr("Wysyłka nie powiodła się. Spróbuj ponownie.");
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
