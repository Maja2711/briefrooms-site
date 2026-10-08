'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..');
const pwaSource = fs.readFileSync(path.join(root, 'scripts/pwa.js'), 'utf8');

class FakeElement {
  constructor(tag = 'div') {
    this.tagName = tag;
    this.children = [];
    this.parentElement = null;
    this.dataset = {};
    this.attributes = {};
  }

  appendChild(child) {
    if (child.parentElement) {
      child.parentElement.children = child.parentElement.children.filter(item => item !== child);
    }
    this.children.push(child);
    child.parentElement = this;
    return child;
  }

  insertBefore(child, sibling) {
    if (child.parentElement) {
      child.parentElement.children = child.parentElement.children.filter(item => item !== child);
    }
    const index = this.children.indexOf(sibling);
    assert.notEqual(index, -1, 'reference element must belong to container');
    this.children.splice(index, 0, child);
    child.parentElement = this;
  }

  get nextElementSibling() {
    if (!this.parentElement) return null;
    return this.parentElement.children[this.parentElement.children.indexOf(this) + 1] || null;
  }

  setAttribute(key, value) { this.attributes[key] = value; }
  addEventListener() {}
  querySelector() { return null; }
}

function bootstrap({ mobile = false, homepage = true } = {}) {
  const head = new FakeElement('head');
  const body = new FakeElement('body');
  const labHead = new FakeElement('div');
  const eyebrow = new FakeElement('span');
  if (homepage) {
    body.appendChild(labHead);
    labHead.appendChild(eyebrow);
  }
  const events = new Map();
  const document = {
    head,
    body,
    documentElement: { lang: 'pl' },
    createElement: tag => new FakeElement(tag),
    querySelector: selector => selector === '.home-lab__head' && homepage
      ? labHead
      : selector === '.home-lab__eyebrow' && homepage ? eyebrow : null
  };
  labHead.querySelector = selector => selector === '.home-lab__eyebrow' ? eyebrow : null;

  const navigator = { userAgent: 'Chrome', platform: 'Win32', maxTouchPoints: 0 };
  const window = {
    navigator,
    matchMedia: query => ({ matches: query.includes('max-width: 680px') ? mobile : false }),
    addEventListener: (event, handler) => events.set(event, handler),
    dispatchEvent() {}
  };

  vm.runInNewContext(pwaSource, { window, document, navigator }, { filename: 'scripts/pwa.js' });
  events.get('load')();
  const installButton = [...body.children, ...labHead.children]
    .find(element => element.className === 'br-pwa-install');
  assert.ok(installButton, 'install button must be created');

  return { installButton, body, labHead, eyebrow, style: head.children[0].textContent, events };
}

test('desktop homepage install badge is anchored to Lab and not fixed to viewport', () => {
  const { installButton, labHead, eyebrow, style } = bootstrap();
  assert.equal(installButton.parentElement, labHead);
  assert.equal(installButton.nextElementSibling, eyebrow);
  assert.equal(installButton.dataset.placement, 'home-lab-desktop');
  assert.match(style, /\.home-lab__head\s*\{position:relative\}/);
  assert.match(style, /\[data-placement="home-lab-desktop"\]\s*\{[^}]*position:absolute;[^}]*top:-22px;[^}]*right:0;/);
});

test('mobile homepage keeps the existing in-flow install badge', () => {
  const { installButton, labHead, style } = bootstrap({ mobile: true });
  assert.equal(installButton.parentElement, labHead);
  assert.equal(installButton.dataset.placement, 'home-lab-mobile');
  assert.match(style, /\[data-placement="home-lab-mobile"\]\s*\{[^}]*position:static;/);
});

test('non-homepage fallback remains a viewport-fixed install badge', () => {
  const { installButton, body, style } = bootstrap({ homepage: false });
  assert.equal(installButton.parentElement, body);
  assert.equal(installButton.dataset.placement, 'floating-desktop');
  assert.match(style, /\.br-pwa-install\s*\{\s*position:fixed;/);
});

test('homepages request the updated, cache-busted PWA script', () => {
  for (const lang of ['pl', 'en']) {
    const html = fs.readFileSync(path.join(root, lang, 'index.html'), 'utf8');
    assert.match(html, /<script src="\/scripts\/pwa\.js\?v=7" defer><\/script>/);
  }
});
