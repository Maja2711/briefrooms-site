// Dedicated, prepared-only EURUSD CB cache. It neither polls sources nor trades.
// Python owns canonical JSON integrity; this transport hashes EXACT received bytes.
export const MAX_BYTES = 3_000_000;
const CHUNK_BYTES = 60_000;
const STALE_AFTER_MS = 15 * 60 * 1000;
const encoder = new TextEncoder();

export async function sha256(bytes) {
  return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)), value => value.toString(16).padStart(2, '0')).join('');
}

async function authorized(request, env) {
  const token = env.EURUSD_CB_WIRE_TOKEN;
  if (typeof token !== 'string' || token.length < 32) return false;
  const header = request.headers.get('Authorization') || '';
  const supplied = await sha256(encoder.encode(header));
  const expected = await sha256(encoder.encode(`Bearer ${token}`));
  let difference = 0;
  for (let i = 0; i < expected.length; i += 1) difference |= supplied.charCodeAt(i) ^ expected.charCodeAt(i);
  return difference === 0;
}

function json(value, status = 200) {
  return new Response(JSON.stringify(value), {status, headers: {'Content-Type': 'application/json', 'Cache-Control': 'no-store'}});
}

async function boundedBody(request) {
  const length = request.headers.get('Content-Length');
  if (length && (!/^\d+$/.test(length) || Number(length) > MAX_BYTES)) throw new Error('TOO_LARGE');
  const reader = request.body?.getReader();
  if (!reader) throw new Error('INVALID_BODY');
  const chunks = [];
  let total = 0;
  while (true) {
    const {value, done} = await reader.read();
    if (done) break;
    total += value.byteLength;
    if (total > MAX_BYTES) { await reader.cancel(); throw new Error('TOO_LARGE'); }
    chunks.push(value);
  }
  const body = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) { body.set(chunk, offset); offset += chunk.byteLength; }
  return body;
}

function base64(bytes) {
  let text = '';
  for (let i = 0; i < bytes.length; i += 1) text += String.fromCharCode(bytes[i]);
  return btoa(text);
}

function fromBase64(text) {
  return Uint8Array.from(atob(text), char => char.charCodeAt(0));
}

function timestamp(value) {
  return typeof value === 'string' && /(?:Z|[+-]\d\d:\d\d)$/.test(value) ? Date.parse(value) : NaN;
}

export class EurusdCbWire {
  constructor(ctx, env) { this.ctx = ctx; this.env = env; }

  async fetch(request) {
    const url = new URL(request.url);
    if (url.protocol !== 'https:' || url.pathname !== '/v1/wire' || url.search) return json({error: 'NOT_FOUND'}, 404);
    if (!await authorized(request, this.env)) return json({error: 'UNAUTHORIZED'}, 401);
    if (request.method === 'GET') return this.read();
    if (request.method !== 'POST') return json({error: 'METHOD_NOT_ALLOWED'}, 405);
    let bytes, payload;
    try {
      bytes = await boundedBody(request);
      payload = JSON.parse(new TextDecoder('utf-8', {fatal: true}).decode(bytes));
    } catch (error) { return json({error: error.message === 'TOO_LARGE' ? 'PAYLOAD_TOO_LARGE' : 'INVALID_JSON'}, error.message === 'TOO_LARGE' ? 413 : 400); }
    const generated = timestamp(payload.generated_at), asOf = timestamp(payload.as_of);
    if (payload.schema_version !== 'eurusd-central-bank-wire-v1' || !Number.isFinite(generated)
        || !Number.isFinite(asOf) || asOf > generated || generated > Date.now()
        || !['FRESH', 'PARTIAL', 'STALE', 'UNAVAILABLE'].includes(payload.overall_status)
        || !/^[a-f0-9]{64}$/.test(payload.integrity?.snapshot_sha256 || '')
        || !Array.isArray(payload.events) || payload.events.length > 1000
        || payload.events.some(row => !['FED', 'ECB'].includes(row.bank))) {
      return json({error: 'INVALID_WIRE_ENVELOPE'}, 400);
    }
    const hash = await sha256(bytes);
    if (request.headers.get('X-Body-SHA256') !== hash) return json({error: 'BODY_HASH_MISMATCH'}, 400);
    const receipt = {body_sha256: hash, generated_at: payload.generated_at,
      received_at: new Date().toISOString(), byte_length: bytes.length,
      chunks: Math.ceil(bytes.length / CHUNK_BYTES), wire_health: payload.overall_status,
      canonical_snapshot_sha256: payload.integrity.snapshot_sha256};
    const outcome = await this.ctx.storage.transaction(async transaction => {
      const latest = await transaction.get('latest');
      if (latest && timestamp(latest.generated_at) > generated) return 'OLDER';
      if (latest && timestamp(latest.generated_at) === generated) return latest.body_sha256 === hash ? 'SAME' : 'CONFLICT';
      const existing = await transaction.get(`receipt:${hash}`);
      if (!existing) {
        for (let i = 0; i < receipt.chunks; i += 1) {
          await transaction.put(`body:${hash}:${i}`, base64(bytes.subarray(i * CHUNK_BYTES, (i + 1) * CHUNK_BYTES)));
        }
        await transaction.put(`receipt:${hash}`, receipt);
      }
      await transaction.put('latest', existing || receipt);
      return 'SAVED';
    });
    if (outcome === 'OLDER' || outcome === 'CONFLICT') return json({error: 'NON_MONOTONIC_SNAPSHOT'}, 409);
    const stored = await this.ctx.storage.get(`receipt:${hash}`);
    return json({...stored, status: outcome === 'SAME' ? 'IDEMPOTENT' : 'SAVED',
      canonical_validation: 'PYTHON_CONSUMER_REQUIRED'}, outcome === 'SAME' ? 200 : 201);
  }

  async read() {
    const latest = await this.ctx.storage.get('latest');
    if (!latest) return json({error: 'UNAVAILABLE'}, 503);
    const chunks = [];
    for (let i = 0; i < latest.chunks; i += 1) {
      const value = await this.ctx.storage.get(`body:${latest.body_sha256}:${i}`);
      if (typeof value !== 'string') return json({error: 'CACHE_INTEGRITY_FAILURE'}, 503);
      chunks.push(fromBase64(value));
    }
    const bytes = new Uint8Array(latest.byte_length);
    let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
    if (offset !== latest.byte_length || await sha256(bytes) !== latest.body_sha256) return json({error: 'CACHE_INTEGRITY_FAILURE'}, 503);
    const age = Date.now() - timestamp(latest.generated_at);
    return new Response(bytes, {headers: {'Content-Type': 'application/json', 'Cache-Control': 'no-store',
      'X-Body-SHA256': latest.body_sha256, 'X-Received-At': latest.received_at,
      'X-Transport-Freshness': age >= 0 && age <= STALE_AFTER_MS ? 'FRESH' : 'STALE',
      'X-Wire-Health': latest.wire_health, 'X-Canonical-Validation': 'PYTHON_CONSUMER_REQUIRED'}});
  }
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.protocol !== 'https:' || url.pathname !== '/v1/wire' || url.search) return json({error: 'NOT_FOUND'}, 404);
    if (!await authorized(request, env)) return json({error: 'UNAUTHORIZED'}, 401);
    if (!env.EURUSD_CB_WIRE) return json({error: 'NAMESPACE_NOT_PROVISIONED'}, 503);
    const singleton = env.EURUSD_CB_WIRE.idFromName('eurusd-central-bank-wire-v1');
    return env.EURUSD_CB_WIRE.get(singleton).fetch(request);
  },
};
