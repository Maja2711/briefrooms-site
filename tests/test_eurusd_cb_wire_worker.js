// Synthetic envelopes only. Worker transport never claims Python source validation.
import test from 'node:test';
import assert from 'node:assert/strict';
import worker, {EurusdCbWire, MAX_BYTES, sha256} from '../workers/eurusd-cb-wire/src/index.js';

const URL = 'https://eurusd-cb-wire.synthetic.workers.dev/v1/wire';
const TOKEN = 'synthetic-unit-test-token-only-32-characters';
const env = {EURUSD_CB_WIRE_TOKEN: TOKEN};
const authorization = `Bearer ${TOKEN}`;

class Storage {
  constructor() { this.values = new Map(); }
  async get(key) { return structuredClone(this.values.get(key)); }
  async put(key, value) {
    assert.ok(new TextEncoder().encode(JSON.stringify(value)).length < 128 * 1024, 'bounded DO value');
    this.values.set(key, structuredClone(value));
  }
  async transaction(callback) {
    const previous = structuredClone(this.values);
    try { return await callback(this); } catch (error) { this.values = previous; throw error; }
  }
}

function instance() { const storage = new Storage(); return {storage, cache: new EurusdCbWire({storage}, env)}; }

function payload(age = 0, extra = {}) {
  const at = new Date(Date.now() - 1000 - age).toISOString();
  return {schema_version: 'eurusd-central-bank-wire-v1', generated_at: at, as_of: at,
    overall_status: 'PARTIAL', events: [], integrity: {snapshot_sha256: 'a'.repeat(64)}, ...extra};
}

async function publication(value, extraHeaders = {}) {
  const body = JSON.stringify(value);
  return new Request(URL, {method: 'POST', body, headers: {'Content-Type': 'application/json',
    Authorization: authorization, 'X-Body-SHA256': await sha256(new TextEncoder().encode(body)), ...extraHeaders}});
}

test('authentication and paths exclude all trading routes', async () => {
  const {cache, storage} = instance();
  assert.equal((await cache.fetch(new Request(URL))).status, 401);
  assert.equal((await cache.fetch(new Request(URL, {headers: {Authorization: 'Bearer wrong'}}))).status, 401);
  assert.equal((await cache.fetch(new Request(URL.replace('/v1/wire', '/sync-trading'), {headers: {Authorization: authorization}}))).status, 404);
  assert.equal(storage.values.size, 0);
});

test('exact raw bytes, immutable receipt and transport health survive cache', async () => {
  const {cache, storage} = instance();
  const request = await publication(payload());
  const raw = await request.clone().text();
  const saved = await cache.fetch(request);
  assert.equal(saved.status, 201);
  const receipt = await saved.json();
  assert.equal(receipt.body_sha256, await sha256(new TextEncoder().encode(raw)));
  assert.equal(receipt.canonical_validation, 'PYTHON_CONSUMER_REQUIRED');
  const response = await cache.fetch(new Request(URL, {headers: {Authorization: authorization}}));
  assert.equal(await response.text(), raw);
  assert.equal(response.headers.get('X-Body-SHA256'), receipt.body_sha256);
  assert.equal(response.headers.get('X-Wire-Health'), 'PARTIAL');
  assert.equal(response.headers.get('X-Transport-Freshness'), 'FRESH');
  assert.ok(storage.values.has(`receipt:${receipt.body_sha256}`));
});

test('idempotent replay preserves received time and rejects equal-time conflict', async () => {
  const {cache, storage} = instance(); const value = payload();
  const first = await (await cache.fetch(await publication(value))).json();
  const before = structuredClone(storage.values);
  const again = await cache.fetch(await publication(value));
  assert.equal(again.status, 200);
  assert.equal((await again.json()).received_at, first.received_at);
  assert.deepEqual(storage.values, before);
  assert.equal((await cache.fetch(await publication({...value, overall_status: 'UNAVAILABLE'}))).status, 409);
  assert.deepEqual(storage.values, before);
});

test('older snapshots cannot replace latest and prior receipts stay immutable', async () => {
  const {cache, storage} = instance();
  await cache.fetch(await publication(payload()));
  const before = structuredClone(storage.values);
  assert.equal((await cache.fetch(await publication(payload(60_000)))).status, 409);
  assert.deepEqual(storage.values, before);
});

test('future timestamps, invalid hashes and other banks cannot enter cache', async () => {
  const {cache, storage} = instance();
  const future = new Date(Date.now() + 60_000).toISOString();
  assert.equal((await cache.fetch(await publication(payload(0, {generated_at: future, as_of: future})))).status, 400);
  assert.equal((await cache.fetch(await publication(payload(), {'X-Body-SHA256': '0'.repeat(64)}))).status, 400);
  assert.equal((await cache.fetch(await publication(payload(0, {events: [{bank: 'BOJ'}]})))).status, 400);
  assert.equal(storage.values.size, 0);
});

test('stale cache remains readable with its true age and health', async () => {
  const {cache} = instance();
  await cache.fetch(await publication(payload(20 * 60_000)));
  const response = await cache.fetch(new Request(URL, {headers: {Authorization: authorization}}));
  assert.equal(response.status, 200);
  assert.equal(response.headers.get('X-Transport-Freshness'), 'STALE');
});

test('Unicode payload is chunked below DO value limits without byte changes', async () => {
  const {cache, storage} = instance(); const value = payload(0, {synthetic_padding: '€ż'.repeat(70_000)});
  const raw = JSON.stringify(value);
  assert.equal((await cache.fetch(await publication(value))).status, 201);
  assert.ok([...storage.values.keys()].filter(key => key.startsWith('body:')).length > 1);
  assert.equal(await (await cache.fetch(new Request(URL, {headers: {Authorization: authorization}}))).text(), raw);
});

test('oversized body and unavailable or corrupt storage fail closed', async () => {
  const {cache, storage} = instance();
  assert.equal((await cache.fetch(new Request(URL, {headers: {Authorization: authorization}}))).status, 503);
  assert.equal((await cache.fetch(await publication(payload(0, {synthetic_padding: 'x'.repeat(MAX_BYTES)})))).status, 413);
  await cache.fetch(await publication(payload()));
  const key = [...storage.values.keys()].find(value => value.startsWith('body:'));
  storage.values.delete(key);
  assert.equal((await cache.fetch(new Request(URL, {headers: {Authorization: authorization}}))).status, 503);
});

test('gateway uses only the dedicated singleton namespace', async () => {
  let claimed;
  const result = await worker.fetch(new Request(URL, {headers: {Authorization: authorization}}), {...env,
    EURUSD_CB_WIRE: {idFromName(name) { claimed = name; return 'id'; },
      get(id) { assert.equal(id, 'id'); return {fetch: async () => new Response('synthetic')}; }}});
  assert.equal(await result.text(), 'synthetic');
  assert.equal(claimed, 'eurusd-central-bank-wire-v1');
  assert.equal((await worker.fetch(new Request(URL, {headers: {Authorization: authorization}}), env)).status, 503);
});
