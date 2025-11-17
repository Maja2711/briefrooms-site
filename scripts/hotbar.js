(function () {
  const lang = document.documentElement.lang || "pl";
  const isPL = lang.startsWith("pl");
  const jsonUrl = isPL
    ? "/.cache/news_summaries_pl.json"
    : "/.cache/news_summaries_en.json";

  const track = document.getElementById("br-hotbar-track");
  const timeEl = document.getElementById("br-hotbar-time");

  if (!track) return;

  // Fallback gdy nie zadziała fetch / JSON jest pusty
  const fallbackItems = isPL
    ? [
        {
          title:
            "Kongres PSL wybierze nowe władze. Kosiniak-Kamysz o roli ludowców w rządzie",
          url: "/pl/aktualnosci.html"
        },
        {
          title:
            "Groźby pod adresem prezydenta Karola Nawrockiego. Policja bada sprawę",
          url: "/pl/aktualnosci.html"
        },
        {
          title:
            "Tysiące osób bez prądu po przejściu Claudii. Krajobraz po burzy",
          url: "/pl/aktualnosci.html"
        }
      ]
    : [
        {
          title: "Key markets and geopolitics in focus today",
          url: "/en/news.html"
        },
        {
          title: "Central banks: latest moves and comments",
          url: "/en/news.html"
        }
      ];

  function renderItems(items) {
    track.innerHTML = "";
    items.forEach((item) => {
      const a = document.createElement("a");
      a.className = "br-hotbar-item";
      a.href =
        item.url ||
        (isPL ? "/pl/aktualnosci.html" : "/en/news.html");
      a.textContent = item.title;
      track.appendChild(a);
    });
  }

  function renderTime(updatedAt) {
    if (!timeEl || !updatedAt) return;
    const d = new Date(updatedAt);
    if (Number.isNaN(d.getTime())) return;
    const locale = isPL ? "pl-PL" : "en-GB";
    timeEl.textContent = d.toLocaleTimeString(locale, {
      hour: "2-digit",
      minute: "2-digit"
    });
  }

  // Główne pobieranie JSON-a
  fetch(jsonUrl, { cache: "no-store" })
    .then((res) => {
      if (!res.ok) throw new Error("HTTP " + res.status);
      return res.json();
    })
    .then((data) => {
      let items = [];

      // Zakładany format: { updated_at, items:[{title,url,is_hot}] }
      if (data && Array.isArray(data.items) && data.items.length) {
        const hot = data.items.filter((it) => it.is_hot);
        items = hot.length ? hot : data.items;
        renderTime(data.updated_at);
      } else {
        items = fallbackItems;
      }

      renderItems(items);
    })
    .catch(() => {
      // Fallback przy błędzie
      renderItems(fallbackItems);
    });
})();
