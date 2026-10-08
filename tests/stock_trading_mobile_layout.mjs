import assert from "node:assert/strict";
import { pathToFileURL } from "node:url";

const driverPath = process.env.PLAYWRIGHT_CORE_ROOT
  || "/tmp/briefrooms-mobile-browser/node_modules/playwright-core/index.mjs";
const { chromium } = await import(pathToFileURL(driverPath).href);
const browser = await chromium.launch({
  channel: "chrome",
  headless: true,
  args: ["--no-sandbox", "--disable-dev-shm-usage"],
});
const base = process.env.MOBILE_LAYOUT_BASE_URL || "http://127.0.0.1:8765";
const widths = [320, 360, 390, 430, 768];
try {
  for (const lang of ["pl", "en"]) {
    const route = lang === "pl" ? "/pl/inwestycje/stock-trading.html" : "/en/investing/stock-trading.html";
    for (const width of [...widths, 1280]) {
      const context = await browser.newContext({
        viewport: { width, height: 812 },
        isMobile: width <= 768,
        hasTouch: width <= 768,
        deviceScaleFactor: 2,
      });
      const page = await context.newPage();
      const errors = [];
      page.on("pageerror", (err) => errors.push(err.message));
      try {
        await page.goto(base + route, { waitUntil: "domcontentloaded", timeout: 40_000 });
        await page.locator(".investments-primary-nav").waitFor();
        await page.locator(".stock-room-hero").waitFor();
        await page.waitForTimeout(350);
        const sizes = await page.evaluate(() => {
          const history = document.querySelector(".str-history-table");
          if (!history) {
            const fixture = document.createElement("div");
            fixture.className = "str-history-section str-section";
            fixture.innerHTML = '<div class="str-history-table"><div class="str-history-head">' +
              '<span>GPW</span>'.repeat(12) + '</div></div>';
            document.getElementById("stock-trading-portfolio-root").appendChild(fixture);
          }
          const nav = document.querySelector(".investments-primary-nav");
          const table = document.querySelector(".str-history-table");
          const bounds = (selector) => {
            const rect = document.querySelector(selector).getBoundingClientRect();
            return { left: rect.left, right: rect.right, width: rect.width };
          };
          const initial = {
            viewport: document.documentElement.clientWidth,
            scrollWidth: document.documentElement.scrollWidth,
            rootScrollWidth: document.scrollingElement.scrollWidth,
            htmlOverflow: getComputedStyle(document.documentElement).overflowX,
            bodyOverflow: getComputedStyle(document.body).overflowX,
            shell: bounds(".stock-room-shell"),
            hero: bounds(".stock-room-hero"),
            header: bounds("#site-header"),
            portfolio: bounds("#stock-trading-portfolio-root"),
            nav: { width: nav.clientWidth, scrollWidth: nav.scrollWidth },
            table: { width: table.clientWidth, scrollWidth: table.scrollWidth },
          };
          // Both nested panes must scroll independently, without panning
          // the outer document (the defect reported on Samsung PWA).
          nav.scrollLeft = nav.scrollWidth;
          table.scrollLeft = table.scrollWidth;
          scrollTo({ left: 2000, top: 0, behavior: "instant" });
          document.documentElement.scrollLeft = 2000;
          document.body.scrollLeft = 2000;
          return { ...initial, documentScrollX: scrollX, navScrollLeft: nav.scrollLeft, tableScrollLeft: table.scrollLeft };
        });
        assert.ok(sizes.viewport > 0, "missing viewport");
        if (width <= 768) {
          assert.equal(sizes.htmlOverflow, "clip", `${lang}/${width}: document x overflow not clipped`);
          assert.equal(sizes.bodyOverflow, "clip", `${lang}/${width}: body x overflow not clipped`);
          assert.equal(sizes.documentScrollX, 0, `${lang}/${width}: whole page moved horizontally`);
          assert.ok(sizes.scrollWidth <= sizes.viewport + 2, `${lang}/${width}: document overflow ${JSON.stringify(sizes)}`);
          for (const key of ["shell", "hero", "header", "portfolio"]) {
            assert.ok(sizes[key].left >= -2 && sizes[key].right <= sizes.viewport + 2,
              `${lang}/${width}: ${key} escapes viewport: ${JSON.stringify(sizes[key])}`);
          }
          if (width <= 430) {
            assert.ok(sizes.nav.scrollWidth > sizes.nav.width + 15, `${lang}/${width}: nav cannot scroll`);
            assert.ok(sizes.navScrollLeft > 0, `${lang}/${width}: nav horizontal scroll broken`);
          }
          assert.ok(sizes.tableScrollLeft > 0, `${lang}/${width}: history horizontal scroll broken`);
        } else {
          assert.notEqual(sizes.htmlOverflow, "clip", `${lang}/desktop: desktop overflow unexpectedly altered`);
        }
        console.log(`PASS ${lang.toUpperCase()} ${width}px: docX=${sizes.documentScrollX} viewport=${sizes.viewport} scrollWidth=${sizes.scrollWidth} tabsX=${sizes.navScrollLeft} historyX=${sizes.tableScrollLeft}`);
      } finally {
        await context.close();
      }
    }
  }
} finally {
  await browser.close();
}
