import assert from "node:assert/strict";
import { pathToFileURL } from "node:url";

const playwrightRoot = process.env.PLAYWRIGHT_CORE_ROOT || "/tmp/briefrooms-pwa-browser/node_modules/playwright-core/index.mjs";
const { chromium } = await import(pathToFileURL(playwrightRoot).href);

const browser = await chromium.launch({
  channel: "chrome",
  headless: true,
  args: ["--no-sandbox", "--disable-dev-shm-usage"],
});

try {
  for (const lang of ["pl", "en"]) {
    const page = await browser.newPage({ viewport: { width: 1648, height: 900 } });
    const url = `https://briefrooms.com/${lang}/?pwa_scroll_probe=${Date.now()}`;

    await page.goto(url, { waitUntil: "domcontentloaded", timeout: 45_000 });
    await page.waitForSelector(".br-pwa-install", { state: "attached", timeout: 30_000 });

    const initial = await page.evaluate(() => {
      const button = document.querySelector(".br-pwa-install");
      button.dataset.visible = "true"; // Works even without an OS install prompt.
      const rect = button.getBoundingClientRect();
      return {
        position: getComputedStyle(button).position,
        placement: button.dataset.placement,
        insideLab: Boolean(button.closest(".home-lab__head")),
        buttonTop: rect.top,
        buttonRight: rect.right,
        totalScroll: document.scrollingElement.scrollHeight - innerHeight,
        viewportWidth: innerWidth,
      };
    });

    assert.equal(initial.position, "absolute", `${lang}: install badge must scroll with Lab, not float on screen`);
    assert.equal(initial.placement, "home-lab-desktop", `${lang}: unexpected placement`);
    assert.equal(initial.insideLab, true, `${lang}: badge is outside Lab header`);
    assert.ok(initial.totalScroll > 500, `${lang}: homepage has insufficient height to verify scrolling`);

    await page.evaluate(() => scrollTo(0, 650));
    await page.waitForTimeout(120);
    const after = await page.evaluate(() => ({
      scrollY,
      top: document.querySelector(".br-pwa-install").getBoundingClientRect().top,
      position: getComputedStyle(document.querySelector(".br-pwa-install")).position,
    }));
    assert.ok(after.scrollY >= 600, `${lang}: browser did not scroll far enough`);
    assert.ok(
      Math.abs((initial.buttonTop - after.top) - after.scrollY) < 6,
      `${lang}: install button followed viewport instead of scrolling with document`
    );
    assert.equal(after.position, "absolute", `${lang}: CSS changed during scrolling`);
    console.log(`PASS ${lang.toUpperCase()}: install button scrolls with Lab; before=${initial.buttonTop.toFixed(1)}px after=${after.top.toFixed(1)}px scrollY=${after.scrollY}px`);
    await page.close();
  }
} finally {
  await browser.close();
}
