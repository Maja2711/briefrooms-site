(() => {
  "use strict";

  let deferredPrompt = null;
  let installButton = null;
  let iosHelp = null;

  const lang = (document.documentElement.lang || "pl").toLowerCase().startsWith("pl") ? "pl" : "en";
  const copy = lang === "pl" ? {
    install: "BRs · Zainstaluj",
    installing: "Otwieram instalację…",
    iosTitle: "Zainstaluj BriefRooms",
    iosBody: "Na iPhonie/iPadzie wybierz Udostępnij, a następnie „Dodaj do ekranu początkowego”.",
    close: "Zamknij"
  } : {
    install: "BRs · Install",
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
        position:fixed;
        top:calc(var(--br-site-header-height, 84px) + 20px);
        right:max(28px,calc((100vw - 1360px)/2 + 20px));
        z-index:2147483000;
        display:none;align-items:center;justify-content:center;
        min-height:34px;padding:0 13px;
        border:1px solid rgba(120,231,247,.42);border-radius:999px;
        background:linear-gradient(180deg,rgba(18,55,84,.90),rgba(5,27,49,.95));
        color:#dffcff;font:820 11.5px/1 Inter,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif;
        letter-spacing:.02em;box-shadow:0 8px 24px rgba(0,0,0,.28),inset 0 1px 0 rgba(255,255,255,.12);
        backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);cursor:pointer;
        white-space:nowrap;
      }
      .br-pwa-install:hover{border-color:#78e7f7;box-shadow:0 10px 28px rgba(0,0,0,.34),0 0 18px rgba(35,213,204,.12),inset 0 1px 0 rgba(255,255,255,.16)}
      .br-pwa-install:focus-visible{outline:2px solid #78e7f7;outline-offset:3px}
      .br-pwa-install img{display:none}
      .br-pwa-install[data-visible="true"]{display:inline-flex}
      #site-header.is-open ~ .br-pwa-install{
        opacity:0;
        visibility:hidden;
        pointer-events:none;
      }
      .br-pwa-ios-help{position:fixed;inset:0;z-index:2147483001;display:none;place-items:center;padding:20px;background:rgba(0,8,18,.68);backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px)}
      .br-pwa-ios-help[data-open="true"]{display:grid}
      .br-pwa-ios-card{width:min(440px,100%);padding:24px;border:1px solid rgba(120,231,247,.32);border-radius:22px;background:#091827;color:#eef7ff;box-shadow:0 24px 80px rgba(0,0,0,.5);font-family:Inter,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif}
      .br-pwa-ios-card h2{margin:0 0 10px;font-size:22px}.br-pwa-ios-card p{margin:0;color:#b7c8d8;line-height:1.55}
      .br-pwa-ios-card button{margin-top:18px;border:1px solid rgba(120,231,247,.34);border-radius:999px;background:rgba(35,213,204,.1);color:#eaffff;padding:9px 14px;font-weight:800}
      @media(max-width:1050px){
        .br-pwa-install{
          top:calc(var(--br-site-header-height, 84px) + 20px);
          right:18px;
        }
      }
      @media(max-width:680px){
        .br-pwa-install{
          position:fixed;
          top:calc(var(--br-site-header-height, 68px) + 44px + env(safe-area-inset-top, 0px));
          left:14px;
          right:auto;
          bottom:auto;
          min-height:38px;
          padding:0 12px;
          font-size:11.5px;
          justify-content:center;
        }
      }
    `;
    document.head.appendChild(style);

    installButton = document.createElement("button");
    installButton.type = "button";
    installButton.className = "br-pwa-install";
    installButton.setAttribute("aria-label", copy.install);
    installButton.innerHTML = `<span>${copy.install}</span>`;
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
