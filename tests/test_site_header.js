const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const header = require('../scripts/site-header.js');
const root = path.resolve(__dirname, '..');

class FakeClassList {
  constructor(owner) { this.owner = owner; }
  values() { return new Set((this.owner.className || '').split(/\s+/).filter(Boolean)); }
  add(...names) { const values = this.values(); names.forEach((name) => values.add(name)); this.owner.className = [...values].join(' '); }
  remove(...names) { const values = this.values(); names.forEach((name) => values.delete(name)); this.owner.className = [...values].join(' '); }
  contains(name) { return this.values().has(name); }
}

class FakeElement {
  constructor(tagName, document) {
    this.tagName = tagName.toUpperCase();
    this.ownerDocument = document;
    this.children = [];
    this.attributes = {};
    this.listeners = {};
    this.className = '';
    this.classList = new FakeClassList(this);
    this.parentNode = null;
    this.textContent = '';
  }
  appendChild(child) { child.parentNode = this; this.children.push(child); return child; }
  insertBefore(child, reference) {
    child.parentNode = this;
    const index = reference ? this.children.indexOf(reference) : -1;
    if (index < 0) this.children.push(child); else this.children.splice(index, 0, child);
    return child;
  }
  setAttribute(name, value) { this.attributes[name] = String(value); if (name === 'id') this.id = String(value); }
  getAttribute(name) { return Object.prototype.hasOwnProperty.call(this.attributes, name) ? this.attributes[name] : null; }
  addEventListener(type, listener) { (this.listeners[type] ||= []).push(listener); }
  emit(type, event = {}) { (this.listeners[type] || []).forEach((listener) => listener(event)); }
  focus() { this.ownerDocument.activeElement = this; }
  contains(candidate) { return this === candidate || this.children.some((child) => child.contains(candidate)); }
  closest(selector) {
    if (selector === 'a' && this.tagName === 'A') return this;
    return this.parentNode ? this.parentNode.closest(selector) : null;
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  querySelectorAll(selector) {
    const matches = [];
    const visit = (element) => {
      const isMatch = selector === 'a' ? element.tagName === 'A'
        : selector.startsWith('#') ? element.id === selector.slice(1)
          : selector.startsWith('.') ? element.classList.contains(selector.slice(1)) : false;
      if (isMatch) matches.push(element);
      element.children.forEach(visit);
    };
    this.children.forEach(visit);
    return matches;
  }
  get firstChild() { return this.children[0] || null; }
}

class FakeDocument {
  constructor(language = 'pl') {
    this.listeners = {};
    this.activeElement = null;
    this.documentElement = new FakeElement('html', this);
    this.documentElement.lang = language;
    this.body = new FakeElement('body', this);
    this.documentElement.appendChild(this.body);
  }
  createElement(tagName) { return new FakeElement(tagName, this); }
  getElementById(id) { return this.documentElement.querySelector(`#${id}`); }
  querySelector() { return null; }
  addEventListener(type, listener) { (this.listeners[type] ||= []).push(listener); }
  emit(type, event = {}) { (this.listeners[type] || []).forEach((listener) => listener(event)); }
}

function fakeWindow() {
  return {
    requestAnimationFrame(callback) { callback(); },
    matchMedia() { return { addEventListener() {} }; },
  };
}

function localFile(route) {
  const relative = route.endsWith('/') ? `${route.slice(1)}index.html` : route.slice(1);
  return path.join(root, ...relative.split('/'));
}

test('PL and EN navigation remain independent and complete', () => {
  assert.deepEqual(header.navigation.pl.map((item) => item.label), ['Aktualności', 'Geopolityka', 'Zdrowie', 'Nauka', 'Inwestycje']);
  assert.deepEqual(header.navigation.en.map((item) => item.label), ['News', 'Geopolitics', 'Health', 'Science', 'Investing']);
  assert.equal(header.navigation.pl.length, 5);
  assert.equal(header.navigation.en.length, 5);
  assert.ok(header.navigation.pl.every((item) => item.href.startsWith('/pl/')));
  assert.ok(header.navigation.en.every((item) => item.href.startsWith('/en/')));
});

test('active sections are detected for hubs and articles', () => {
  const cases = {
    '/pl/aktualnosci.html': 'news',
    '/en/news.html': 'news',
    '/pl/geo/ziemie-rzadkie.html': 'geopolitics',
    '/en/geo/black-sea.html': 'geopolitics',
    '/pl/zdrowie/kalkulator-cholesterolu.html': 'health',
    '/en/science/dark-oxygen.html': 'science',
    '/pl/inwestycje/portfel-10k.html': 'investing',
    '/en/about.html': '',
  };
  for (const [route, expected] of Object.entries(cases)) assert.equal(header.sectionForPath(route), expected, route);
});

test('language counterparts resolve to existing pages and fall back to home', () => {
  for (const [source, target] of Object.entries(header.counterparts)) {
    assert.ok(fs.existsSync(localFile(source)), `missing source ${source}`);
    assert.ok(fs.existsSync(localFile(target)), `missing target ${target}`);
    assert.equal(header.counterpartForPath(source), target);
  }
  assert.equal(header.counterpartForPath('/pl/geo/polska-panstwo-frontowe.html'), '/en/');
  assert.equal(header.counterpartForPath('/en/geo/black-sea.html'), '/pl/');
});

test('rendered header exposes active state, language link and accessible mobile controls', () => {
  const document = new FakeDocument('pl');
  const host = document.createElement('header');
  host.setAttribute('id', 'site-header');
  document.body.appendChild(host);
  const location = {
    pathname: '/pl/nauka/mucholowka-biomechanika.html',
    href: 'https://briefrooms.com/pl/nauka/mucholowka-biomechanika.html',
    origin: 'https://briefrooms.com',
    hostname: 'briefrooms.com',
  };

  header.init(document, location, fakeWindow());
  const nav = document.getElementById('br-site-navigation');
  const links = nav.querySelectorAll('a');
  const active = links.find((link) => link.getAttribute('aria-current') === 'page');
  const toggle = host.querySelector('.br-site-header__toggle');
  const languageLink = host.querySelector('.br-site-header__lang');

  assert.equal(links.length, 5);
  assert.equal(active.textContent, 'Nauka');
  assert.equal(languageLink.href, '/en/science/venus-flytrap-biomechanics.html');
  assert.equal(toggle.getAttribute('aria-controls'), 'br-site-navigation');
  assert.equal(toggle.getAttribute('aria-expanded'), 'false');

  toggle.emit('click');
  assert.ok(host.classList.contains('is-open'));
  assert.equal(toggle.getAttribute('aria-expanded'), 'true');
  assert.equal(document.activeElement, links[0]);

  document.emit('keydown', { key: 'Escape' });
  assert.ok(!host.classList.contains('is-open'));
  assert.equal(toggle.getAttribute('aria-expanded'), 'false');
  assert.equal(document.activeElement, toggle);

  toggle.emit('click');
  nav.emit('click', { target: links[1] });
  assert.ok(!host.classList.contains('is-open'));
});
