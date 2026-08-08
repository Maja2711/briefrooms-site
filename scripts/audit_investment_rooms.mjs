import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { chromium } = require('playwright');

const baseUrl = (process.env.AUDIT_BASE_URL || 'http://127.0.0.1:8000').replace(/\/$/, '');
const outputPath = process.env.AUDIT_OUTPUT_PATH || 'data/investments/investment_room_full_audit.json';
const screenshotDir = process.env.AUDIT_SCREENSHOT_DIR || 'artifacts/investment-room-audit';
const expectedController = process.env.AUDIT_CONTROLLER || 'resilient-v9';
const tabs = ['overview', 'portfolio', 'benchmark', 'agents', 'projections', 'rules', 'brace', 'analytics', 'history'];
const expectedNav = ['news', 'investing', 'health', 'science', 'geopolitics', 'about'];
const pages = [
  { lang: 'pl', path: '/pl/inwestycje/portfel-10k.html', other: '/en/investing/portfolio-10k.html', currency: 'PLN' },
  { lang: 'en', path: '/en/investing/portfolio-10k.html', other: '/pl/inwestycje/portfel-10k.html', currency: 'USD' }
];
const loadingPattern = /loading|ładowanie|checking|sprawdzanie/i;
const placeholderPattern = /^\s*[-—]+(?:\s*(?:zł|PLN|USD|\$))?\s*$/i;

function withTimeout(promise, timeoutMs, label) {
  let timer;
  return Promise.race([
    promise,
    new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error(`${label} timed out after ${timeoutMs} ms`)), timeoutMs);
    })
  ]).finally(() => clearTimeout(timer));
}

function criticalUrl(rawUrl, expectedOrigin) {
  try {
    const url = new URL(rawUrl);
    return url.origin === expectedOrigin && /^(\/scripts\/|\/data\/|\/assets\/|\/pl\/|\/en\/)/.test(url.pathname);
  } catch (_) {
    return false;
  }
}

async function closeSafely(target, label) {
  if (!target) return;
  await withTimeout(target.close(), 5000, label).catch(() => {});
}

async function pointerClick(page, selector, expectedTab) {
  const locator = page.locator(selector).first();
  if (await locator.count() !== 1) return { exists: false, clicked: false };
  await locator.scrollIntoViewIfNeeded({ timeout: 4000 });
  const box = await locator.boundingBox({ timeout: 4000 });
  if (!box) return { exists: true, clicked: false, error: 'no bounding box' };
  const point = { x: box.x + box.width / 2, y: box.y + box.height / 2 };
  const hit = await page.evaluate(({ x, y }) => {
    const node = document.elementFromPoint(x, y);
    return {
      tag: node?.tagName || '',
      tab: node?.closest?.('[data-tab]')?.dataset?.tab || '',
      pointerEvents: node ? getComputedStyle(node).pointerEvents : ''
    };
  }, point);
  await page.mouse.click(point.x, point.y);
  await page.waitForFunction(name => {
    const panel = document.querySelector(`.i10k-panel[data-panel="${name}"]`);
    return document.body.dataset.investmentActiveTab === name
      && location.hash === `#${name}`
      && panel?.classList.contains('active')
      && !panel.hidden
      && getComputedStyle(panel).display !== 'none';
  }, expectedTab, { timeout: 3000 });
  return { exists: true, clicked: true, hit, point: { x: Math.round(point.x), y: Math.round(point.y) } };
}

async function inspectPanel(page, tab) {
  return page.evaluate(name => {
    const panel = document.querySelector(`.i10k-panel[data-panel="${name}"]`);
    if (!panel) return { exists: false };
    const text = (panel.innerText || '').trim();
    const selectors = {
      overview: ['#portfolio-value', '#allocation-list .allocation-row', '#benchmark-bars .bar-row'],
      portfolio: ['#portfolio-table tr'],
      benchmark: ['#benchmark-full .bar-row'],
      agents: ['#agent-cards .aitx-shell', '#agent-cards .aitx-agent-card'],
      projections: ['.projection-policy > div'],
      rules: ['#rules-grid > div'],
      brace: ['#brace-control-root > *', '#brace-summary > *', '#brace-positions > *'],
      analytics: ['#kpis > *', '#chart > *', '#positions > *'],
      history: ['#reviews > *', '#audit-body > tr']
    }[name] || [];
    const nodeCounts = Object.fromEntries(selectors.map(selector => [selector, panel.querySelectorAll(selector).length]));
    const minimums = { agents: 5 };
    const requiredNodesReady = selectors.every(selector => {
      const count = nodeCounts[selector];
      return name === 'agents' && selector.includes('aitx-agent-card') ? count === minimums.agents : count > 0;
    });
    return {
      exists: true,
      active: panel.classList.contains('active'),
      visible: !panel.hidden && getComputedStyle(panel).display !== 'none' && panel.getClientRects().length > 0,
      hidden: panel.hidden,
      ariaHidden: panel.getAttribute('aria-hidden'),
      contentLength: text.length,
      containsLoadingPlaceholder: /loading|ładowanie|checking|sprawdzanie/i.test(text),
      requiredNodesReady,
      nodeCounts,
      hash: location.hash,
      bodyActive: document.body.dataset.investmentActiveTab || '',
      guard: document.body.dataset.investmentNavigationGuard || ''
    };
  }, tab);
}

async function verifyTab(page, selector, tab) {
  const result = { tab, selector, passed: false };
  try {
    Object.assign(result, await pointerClick(page, selector, tab));
    Object.assign(result, await inspectPanel(page, tab));
    result.passed = Boolean(
      result.exists && result.clicked && result.hit?.tab === tab && result.hit.pointerEvents !== 'none'
      && result.active && result.visible && !result.hidden && result.hash === `#${tab}`
      && result.bodyActive === tab && result.guard === 'active-v2'
      && result.contentLength >= 20 && !result.containsLoadingPlaceholder && result.requiredNodesReady
    );
  } catch (error) {
    result.error = String(error?.message || error);
  }
  return result;
}

async function auditPage(browser, spec) {
  const context = await browser.newContext({
    viewport: { width: 1680, height: 936 },
    locale: spec.lang === 'pl' ? 'pl-PL' : 'en-US'
  });
  const page = await context.newPage();
  page.setDefaultTimeout(5000);
  const expectedOrigin = new URL(baseUrl).origin;
  const consoleErrors = [];
  const pageErrors = [];
  const requestFailures = [];
  const httpErrors = [];
  page.on('console', message => { if (message.type() === 'error') consoleErrors.push(message.text()); });
  page.on('pageerror', error => pageErrors.push(String(error?.stack || error)));
  page.on('requestfailed', request => {
    if (criticalUrl(request.url(), expectedOrigin)) requestFailures.push({ url: request.url(), error: request.failure()?.errorText || 'unknown' });
  });
  page.on('response', response => {
    if (response.status() >= 400 && criticalUrl(response.url(), expectedOrigin)) httpErrors.push({ url: response.url(), status: response.status() });
  });

  const result = {
    language: spec.lang,
    url: `${baseUrl}${spec.path}`,
    passed: false,
    data: {},
    navigation: {},
    languageSwitch: {},
    topTabs: [],
    sidebarTabs: [],
    tournamentCta: {},
    consoleErrors,
    pageErrors,
    criticalRequestFailures: requestFailures,
    criticalHttpErrors: httpErrors
  };

  try {
    const entryResponse = await page.goto(`${baseUrl}${spec.path}?audit=${Date.now()}#overview`, { waitUntil: 'domcontentloaded', timeout: 30000 });
    result.entry = {
      status: entryResponse?.status() || 0,
      url: page.url(),
      contentType: entryResponse?.headers()['content-type'] || ''
    };
    // The tab shell is static HTML. Wait for DOM attachment here; real pointer
    // visibility is verified separately for every top and sidebar control.
    await page.waitForSelector('.i10k-tabs [data-tab="overview"]', { state: 'attached', timeout: 10000 });
    await page.waitForFunction(controller => {
      const value = document.querySelector('#portfolio-value')?.textContent?.trim() || '';
      const status = document.querySelector('#data-status')?.textContent?.trim() || '';
      const positions = document.querySelector('#positions-count')?.textContent?.trim() || '';
      return document.body.dataset.investmentController === controller
        && document.body.dataset.investmentData === 'ready'
        && document.body.dataset.investmentDataSource === 'network'
        && document.body.dataset.investmentNetwork === 'healthy'
        && document.body.dataset.investmentBrace === 'ready'
        && !/loading|ładowanie|checking|sprawdzanie/i.test(status)
        && value && !/^[-—]+/.test(value) && /^\d+$/.test(positions);
    }, expectedController, { timeout: 30000 });
    await page.waitForTimeout(2500);

    result.data = await page.evaluate(() => ({
      status: document.querySelector('#data-status')?.textContent?.trim() || '',
      portfolioValue: document.querySelector('#portfolio-value')?.textContent?.trim() || '',
      positions: document.querySelector('#positions-count')?.textContent?.trim() || '',
      controller: document.body.dataset.investmentController || '',
      source: document.body.dataset.investmentDataSource || '',
      network: document.body.dataset.investmentNetwork || '',
      brace: document.body.dataset.investmentBrace || '',
      currency: document.body.dataset.investmentCurrency || ''
    }));
    result.data.loaded = !loadingPattern.test(result.data.status)
      && Boolean(result.data.portfolioValue) && !placeholderPattern.test(result.data.portfolioValue)
      && /^\d+$/.test(result.data.positions) && result.data.controller === expectedController
      && result.data.source === 'network' && result.data.network === 'healthy'
      && result.data.brace === 'ready' && result.data.currency === spec.currency;

    const order = await page.locator('#site-header .br-site-header__nav > a[data-section]').evaluateAll(nodes => nodes.map(node => node.dataset.section));
    result.navigation = { order, expected: expectedNav, passed: JSON.stringify(order) === JSON.stringify(expectedNav) };
    result.languageSwitch = await page.locator('#site-header .br-site-header__lang').first().evaluate((node, expected) => ({
      href: new URL(node.href).pathname,
      hreflang: node.hreflang,
      passed: new URL(node.href).pathname === expected
    }), spec.other).catch(error => ({ passed: false, error: String(error?.message || error) }));

    for (const tab of tabs) result.topTabs.push(await verifyTab(page, `.i10k-tabs [data-tab="${tab}"]`, tab));
    for (const tab of tabs) result.sidebarTabs.push(await verifyTab(page, `.i10k-side-nav [data-tab="${tab}"]`, tab));

    await page.evaluate(() => window.BriefRoomsInvestmentNavigation?.activate('overview', false));
    result.tournamentCta = await verifyTab(page, '.agents-wide [data-tab="agents"]', 'agents');

    result.passed = Boolean(
      result.data.loaded && result.navigation.passed && result.languageSwitch.passed
      && result.topTabs.every(item => item.passed)
      && result.sidebarTabs.every(item => item.passed)
      && result.tournamentCta.passed
      && consoleErrors.length === 0 && pageErrors.length === 0
      && requestFailures.length === 0 && httpErrors.length === 0
    );
  } catch (error) {
    result.fatalError = String(error?.stack || error);
  } finally {
    fs.mkdirSync(screenshotDir, { recursive: true });
    await withTimeout(page.screenshot({ path: path.join(screenshotDir, `${spec.lang}.png`), fullPage: false }), 7000, 'screenshot').catch(() => {});
    await closeSafely(context, `close ${spec.lang} context`);
  }
  return result;
}

async function main() {
  const report = {
    schemaVersion: 'investment-room-production-audit-v9',
    generatedAt: new Date().toISOString(),
    baseUrl,
    expectedController,
    passed: false,
    results: {}
  };
  try {
    for (const spec of pages) {
      process.stdout.write(`Auditing ${spec.lang.toUpperCase()} ${baseUrl}${spec.path}\n`);
      let browser;
      try {
        browser = await chromium.launch({ headless: true });
        report.results[spec.lang] = await withTimeout(auditPage(browser, spec), 75000, `${spec.lang} audit`);
      } catch (error) {
        report.results[spec.lang] = {
          language: spec.lang,
          url: `${baseUrl}${spec.path}`,
          passed: false,
          fatalError: String(error?.stack || error)
        };
      } finally {
        await closeSafely(browser, `close ${spec.lang} browser`);
      }
    }
    report.passed = pages.every(spec => report.results[spec.lang]?.passed);
  } catch (error) {
    report.fatalError = String(error?.stack || error);
  }
  fs.mkdirSync(path.dirname(outputPath), { recursive: true });
  fs.writeFileSync(outputPath, JSON.stringify(report, null, 2) + '\n');
  process.stdout.write(`${JSON.stringify(report, null, 2)}\n`);
  return report.passed ? 0 : 1;
}

process.exit(await main());
