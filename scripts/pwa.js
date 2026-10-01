(() => {
  "use strict";

  let deferredPrompt = null;
  let installButton = null;
  let iosHelp = null;

  const lang = (document.documentElement.lang || "pl").toLowerCase().startsWith("pl") ? "pl" : "en";
  const copy = lang === "pl" ? {
    install: "Zainstaluj BriefRooms",
    installing: "Otwieram instalację…",
    iosTitle: "Zainstaluj BriefRooms",
    iosBody: "Na iPhonie/iPadzie wybierz Udostępnij, a następnie „Dodaj do ekranu początkowego”.",
    close: "Zamknij"
  } : {
    install: "Install BriefRooms",
    installing: "Opening installer…",
    iosTitle: "Install BriefRooms",
    iosBody: "On iPhone/iPad, tap Share and then “Add to Home Screen”.",
    close: "Close"
  };

  const isStandalone = () =>
    window.matchMedia?.("(display-mode: standalone)")?.matches === true ||
    window.navigator.standalone === true;

  const isIos = () =>
    /iphone|ipad|ipod/i.test(navigator.userAgent || "") ||
    (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);

  function ensureUi() {
    if (installButton || isStandalone()) return;

    const style = document.createElement("style");
    style.textContent = `
      .br-pwa-install{
        position:fixed;right:22px;bottom:22px;z-index:2147483000;
        display:none;align-items:center;gap:10px;min-height:48px;padding:0 18px;
        border:1px solid rgba(120,231,247,.55);border-radius:999px;
        background:linear-gradient(180deg,rgba(18,55,84,.96),rgba(5,27,49,.98));
        color:#dffcff;font:850 14px/1 Inter,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif;
        letter-spacing:.01em;box-shadow:0 14px 42px rgba(0,0,0,.38),inset 0 1px 0 rgba(255,255,255,.16);
        backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);cursor:pointer;
      }
      .br-pwa-install:hover{transform:translateY(-1px);border-color:#78e7f7;box-shadow:0 18px 48px rgba(0,0,0,.42),0 0 24px rgba(35,213,204,.16),inset 0 1px 0 rgba(255,255,255,.2)}
      .br-pwa-install:focus-visible{outline:2px solid #78e7f7;outline-offset:3px}
      .br-pwa-install img{width:28px;height:28px;border-radius:9px;display:block}
      .br-pwa-install[data-visible="true"]{display:inline-flex}
      .br-pwa-ios-help{position:fixed;inset:0;z-index:2147483001;display:none;place-items:center;padding:20px;background:rgba(0,8,18,.68);backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px)}
      .br-pwa-ios-help[data-open="true"]{display:grid}
      .br-pwa-ios-card{width:min(440px,100%);padding:24px;border:1px solid rgba(120,231,247,.32);border-radius:22px;background:#091827;color:#eef7ff;box-shadow:0 24px 80px rgba(0,0,0,.5);font-family:Inter,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif}
      .br-pwa-ios-card h2{margin:0 0 10px;font-size:22px}.br-pwa-ios-card p{margin:0;color:#b7c8d8;line-height:1.55}
      .br-pwa-ios-card button{margin-top:18px;border:1px solid rgba(120,231,247,.34);border-radius:999px;background:rgba(35,213,204,.1);color:#eaffff;padding:9px 14px;font-weight:800}
      @media(max-width:680px){.br-pwa-install{right:14px;bottom:14px;left:14px;justify-content:center;min-height:52px}}
    `;
    document.head.appendChild(style);

    installButton = document.createElement("button");
    installButton.type = "button";
    installButton.className = "br-pwa-install";
    installButton.setAttribute("aria-label", copy.install);
    installButton.innerHTML = `<img src="/assets/favicon.svg" alt=""><span>${copy.install}</span>`;
    document.body.appendChild(installButton);

    iosHelp = document.createElement("div");
    iosHelp.className = "br-pwa-ios-help";
    iosHelp.innerHTML = `
      <section class="br-pwa-ios-card" role="dialog" aria-modal="true" aria-labelledby="br-pwa-ios-title">
        <h2 id="br-pwa-ios-title">${copy.iosTitle}</h2>
        <p>${copy.iosBody}</p>
        <button type="button" data-br-pwa-close>${copy.close}</button>
      </section>
    `;
    document.body.appendChild(iosHelp);
    iosHelp.querySelector("[data-br-pwa-close]")?.addEventListener("click", () => {
      iosHelp.dataset.open = "false";
    });
    iosHelp.addEventListener("click", (event) => {
      if (event.target === iosHelp) iosHelp.dataset.open = "false";
    });

    installButton.addEventListener("click", async () => {
      if (deferredPrompt) {
        const label = installButton.querySelector("span");
        if (label) label.textContent = copy.installing;
        installButton.disabled = true;
        try {
          deferredPrompt.prompt();
          const choice = await deferredPrompt.userChoice;
          deferredPrompt = null;
          document.documentElement.removeAttribute("data-pwa-installable");
          if (choice?.outcome !== "accepted") {
            installButton.dataset.visible = "false";
          }
        } finally {
          installButton.disabled = false;
          if (label) label.textContent = copy.install;
        }
        return;
      }
      if (isIos()) iosHelp.dataset.open = "true";
    });
  }

  function syncButton() {
    ensureUi();
    if (!installButton) return;
    const visible = !isStandalone() && (Boolean(deferredPrompt) || isIos());
    installButton.dataset.visible = visible ? "true" : "false";
  }

  window.BriefRoomsPWA = {
    isStandalone,
    canInstall: () => Boolean(deferredPrompt),
    install: async () => {
      if (!deferredPrompt) return { available: false };
      deferredPrompt.prompt();
      const choice = await deferredPrompt.userChoice;
      deferredPrompt = null;
      document.documentElement.removeAttribute("data-pwa-installable");
      syncButton();
      return { available: true, outcome: choice?.outcome || null };
    },
  };

  window.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault();
    deferredPrompt = event;
    document.documentElement.setAttribute("data-pwa-installable", "true");
    syncButton();
    window.dispatchEvent(new CustomEvent("briefrooms:pwa-installable"));
  });

  window.addEventListener("appinstalled", () => {
    deferredPrompt = null;
    document.documentElement.removeAttribute("data-pwa-installable");
    syncButton();
    window.dispatchEvent(new CustomEvent("briefrooms:pwa-installed"));
  });

  if ("serviceWorker" in navigator) {
    window.addEventListener("load", () => {
      navigator.serviceWorker.register("/br-trading-sw.js", { scope: "/" }).catch((error) => {
        console.warn("BriefRooms PWA service worker registration failed:", error?.message || error);
      });
      syncButton();
    }, { once: true });
  } else {
    window.addEventListener("load", syncButton, { once: true });
  }
})();
