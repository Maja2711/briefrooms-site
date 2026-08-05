(() => {
  'use strict';

  const ORDER = ['news', 'investing', 'health', 'science', 'geopolitics', 'about'];
  let observer = null;

  function reorder() {
    const nav = document.querySelector('#site-header .br-site-header__nav');
    if (!nav) return false;

    const links = new Map(
      [...nav.querySelectorAll(':scope > a[data-section]')]
        .map(link => [link.dataset.section, link])
    );

    ORDER.forEach(section => {
      const link = links.get(section);
      if (link) nav.appendChild(link);
    });

    nav.dataset.investmentOrder = 'news-investing-health-science-geopolitics-about';
    return true;
  }

  function start() {
    if (reorder()) return;
    observer = new MutationObserver(() => {
      if (reorder() && observer) {
        observer.disconnect();
        observer = null;
      }
    });
    observer.observe(document.documentElement, { childList: true, subtree: true });
    window.setTimeout(() => {
      if (observer) observer.disconnect();
      observer = null;
      reorder();
    }, 8000);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
})();
