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
    for (const width of [1648, 1024]) {
      const page = await browser.newPage({ viewport: { width, height: 900 } });
      try {
        await page.goto(`https://briefrooms.com/${lang}/?pwa_fixed_probe=${Date.now()}`, {
          waitUntil: "domcontentloaded",
          timeout: 45_000,
        });
        await page.waitForSelector(".br-pwa-install", { state: "attached", timeout: 30_000 });

        const initial = await page.evaluate(() => {
          const button = document.querySelector(".br-pwa-install");
          button.dataset.visible = "true"; // No OS install prompt required.
          const rect = button.getBoundingClientRect();
          const labRight = document.querySelector(".home-lab__head").getBoundingClientRect().right;
          return {
            position: getComputedStyle(button).position,
            placement: button.dataset.placement,
            parentIsBody: button.parentElement === document.body,
            top: rect.top,
            right: rect.right,
            labRight,
            scrollRange: document.scrollingElement.scrollHeight - innerHeight,
          };
        });

        assert.equal(initial.position, "fixed", `${lang}/${width}: button is not fixed`);
        assert.equal(initial.placement, "home-lab-desktop", `${lang}/${width}: wrong desktop placement`);
        assert.ok(initial.parentIsBody, `${lang}/${width}: fixed button should be a body child`);
        assert.ok(initial.scrollRange > 650, `${lang}/${width}: not enough scroll range`);
        assert.ok(Math.abs(initial.right - initial.labRight) <= 3, `${lang}/${width}: button must align with Lab right edge`);

        for (const requestedScroll of [350, 650]) {
          await page.evaluate(y => scrollTo(0, y), requestedScroll);
          await page.waitForTimeout(90);
          const after = await page.evaluate(() => {
            const button = document.querySelector(".br-pwa-install");
            const rect = button.getBoundingClientRect();
            return { scrollY, top: rect.top, right: rect.right, position: getComputedStyle(button).position };
          });
          assert.ok(Math.abs(after.scrollY - requestedScroll) < 4, `${lang}/${width}: page failed to scroll`);
          assert.equal(after.position, "fixed", `${lang}/${width}: CSS changed on scroll`);
          assert.ok(Math.abs(after.top - initial.top) <= 2, `${lang}/${width}: button moved vertically by ${after.top-initial.top}px`);
          assert.ok(Math.abs(after.right - initial.right) <= 2, `${lang}/${width}: button moved horizontally`);
        }
        console.log(`PASS ${lang.toUpperCase()} desktop ${width}px: FROZEN; top=${initial.top.toFixed(1)}px, right=${initial.right.toFixed(1)}px, scroll=650px`);
      } finally {
        await page.close();
      }
    }

    const mobilePage = await browser.newPage({ viewport: { width: 390, height: 844 }, isMobile: true });
    try {
      await mobilePage.goto(`https://briefrooms.com/${lang}/?pwa_mobile_probe=${Date.now()}`, {
        waitUntil: "domcontentloaded",
        timeout: 45_000,
      });
      await mobilePage.waitForSelector(".br-pwa-install", { state: "attached", timeout: 30_000 });
      const mobile = await mobilePage.evaluate(() => {
        const button = document.querySelector(".br-pwa-install");
        return {
          placement: button.dataset.placement,
          position: getComputedStyle(button).position,
          insideLab: Boolean(button.closest(".home-lab__head")),
        };
      });
      assert.equal(mobile.placement, "home-lab-mobile", `${lang}: mobile placement changed`);
      assert.equal(mobile.position, "static", `${lang}: mobile positioning changed`);
      assert.equal(mobile.insideLab, true, `${lang}: mobile button left Lab header`);
      console.log(`PASS ${lang.toUpperCase()} mobile 390px: existing inline placement preserved`);
    } finally {
      await mobilePage.close();
    }
  }
} finally {
  await browser.close();
}
