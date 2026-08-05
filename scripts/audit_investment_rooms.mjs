import fs from 'node:fs';
import { chromium } from 'playwright';

const baseUrl = process.env.AUDIT_BASE_URL || 'http://127.0.0.1:8000';
const tabs = ['overview', 'portfolio', 'benchmark', 'agents', 'projections', 'rules', 'brace', 'analytics', 'history'];
const expectedNav = ['news', 'investing', 'health', 'science', 'geopolitics', 'about'];

const pages = [
  { lang: 'pl', path: '/pl/inwestycje/portfel-10k.html' },
  { lang: 'en', path: '/en/investing/portfolio-10k.html' }
];

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

async function auditPage(browser, spec) {
  const context = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
  const page = await context.newPage();
  const consoleErrors = [];
  const pageErrors = [];
  const requestFailures = [];
  const responses = [];

  page.on('console', msg => {
    if (msg.type() === 'error') consoleErrors.push(msg.text());
  });
  page.on('pageerror', error => pageErrors.push(String(error?.stack || error)));
  page.on('requestfailed', request => requestFailures.push({
    url: request.url(),
    failure: request.failure()?.errorText || 'unknown'
  }));
  page.on('response', response => {
    if (response.status() >= 400) responses.push({ url: response.url(), status: response.status() });
  });

  const result = {
    lang: spec.lang,
    url: `${baseUrl}${spec.path}`,
    navigation: {},
    data: {},
    tabs: [],
    console_errors: consoleErrors,
    page_errors: pageErrors,
    request_failures: requestFailures,
    http_errors: responses,
    passed: false
  };

  try {
    await page.goto(`${baseUrl}${spec.path}?audit=${Date.now()}`, {
      waitUntil: 'domcontentloaded',
      timeout: 30000
    });

    await page.waitForSelector('#site-header .br-site-header__nav', { timeout: 15000 });
    await page.waitForSelector('.i10k-tabs [data-tab="overview"]', { timeout: 15000 });

    // Give all deferred runtimes and local JSON requests time to settle.
    await sleep(6000);

    result.navigation.order = await page.locator('#site-header .br-site-header__nav > a[data-section]')
      .evaluateAll(nodes => nodes.map(node => node.dataset.section));
    result.navigation.expected = expectedNav;
    result.navigation.correct = JSON.stringify(result.navigation.order) === JSON.stringify(expectedNav);

    result.data.status = ((await page.locator('#data-status').textContent().catch(() => '')) || '').trim();
    result.data.portfolio_value = ((await page.locator('#portfolio-value').textContent().catch(() => '')) || '').trim();
    result.data.positions = ((await page.locator('#positions-count').textContent().catch(() => '')) || '').trim();
    result.data.loaded = !/loading|ładowanie/i.test(result.data.status)
      && result.data.portfolio_value !== ''
      && !/^[-—]+(?:\s*(?:zł|USD|\$))?$/.test(result.data.portfolio_value)
      && /^\d+$/.test(result.data.positions);

    for (const tab of tabs) {
      const selector = `.i10k-tabs [data-tab="${tab}"]`;
      const trigger = page.locator(selector).first();
      const exists = await trigger.count() > 0;
      const tabResult = { tab, exists, clicked: false, active: false, visible: false, content_length: 0 };
      if (exists) {
        await trigger.click({ timeout: 10000 });
        await page.waitForTimeout(200);
        tabResult.clicked = true;
        const panel = page.locator(`.i10k-panel[data-panel="${tab}"]`).first();
        tabResult.active = await panel.evaluate(node => node.classList.contains('active')).catch(() => false);
        tabResult.visible = await panel.isVisible().catch(() => false);
        tabResult.content_length = ((await panel.innerText().catch(() => '')) || '').trim().length;
      }
      result.tabs.push(tabResult);
    }

    const allTabsPass = result.tabs.every(item => item.exists && item.clicked && item.active && item.visible && item.content_length > 0);
    result.passed = result.navigation.correct
      && result.data.loaded
      && allTabsPass
      && pageErrors.length === 0
      && requestFailures.length === 0
      && responses.length === 0;
  } catch (error) {
    result.fatal_error = String(error?.stack || error);
  } finally {
    await page.screenshot({
      path: `artifacts/investment-room-${spec.lang}.png`,
      fullPage: true
    }).catch(() => {});
    await context.close();
  }

  return result;
}

fs.mkdirSync('artifacts', { recursive: true });
const browser = await chromium.launch({ headless: true });
const results = [];
for (const spec of pages) results.push(await auditPage(browser, spec));
await browser.close();

const report = {
  schema_version: 'investment-room-full-audit-v1',
  generated_at: new Date().toISOString(),
  base_url: baseUrl,
  passed: results.every(item => item.passed),
  results
};

fs.mkdirSync('data/investments', { recursive: true });
fs.writeFileSync('data/investments/investment_room_full_audit.json', JSON.stringify(report, null, 2) + '\n');
console.log(JSON.stringify(report, null, 2));
process.exitCode = report.passed ? 0 : 1;
