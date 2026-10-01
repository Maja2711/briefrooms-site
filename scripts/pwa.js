(() => {
  "use strict";

  let deferredPrompt = null;

  const isStandalone = () =>
    window.matchMedia?.("(display-mode: standalone)")?.matches === true ||
    window.navigator.standalone === true;

  window.BriefRoomsPWA = {
    isStandalone,
    canInstall: () => Boolean(deferredPrompt),
    install: async () => {
      if (!deferredPrompt) return { available: false };
      deferredPrompt.prompt();
      const choice = await deferredPrompt.userChoice;
      deferredPrompt = null;
      document.documentElement.removeAttribute("data-pwa-installable");
      return { available: true, outcome: choice?.outcome || null };
    },
  };

  window.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault();
    deferredPrompt = event;
    document.documentElement.setAttribute("data-pwa-installable", "true");
    window.dispatchEvent(new CustomEvent("briefrooms:pwa-installable"));
  });

  window.addEventListener("appinstalled", () => {
    deferredPrompt = null;
    document.documentElement.removeAttribute("data-pwa-installable");
    window.dispatchEvent(new CustomEvent("briefrooms:pwa-installed"));
  });

  if ("serviceWorker" in navigator) {
    window.addEventListener("load", () => {
      navigator.serviceWorker.register("/br-trading-sw.js", { scope: "/" }).catch((error) => {
        console.warn("BriefRooms PWA service worker registration failed:", error?.message || error);
      });
    }, { once: true });
  }
})();
