import test from "node:test";
import assert from "node:assert/strict";
import { PushHub, commitSyncErrorCode, githubJson } from "../src/index.js";

const sha = "a".repeat(40);
const parent = "b".repeat(40);
const token = "ghs_fake_DIAGNOSTICS_ONLY";

class Storage {
  constructor() { this.map = new Map(); }
  async get(key) { return this.map.get(key); }
  async put(key, value) { this.map.set(key, structuredClone(value)); }
  async list({ prefix = "" } = {}) { return new Map([...this.map].filter(([key]) => key.startsWith(prefix))); }
}

function readOnlyHub() {
  const storage = new Proxy({}, { get() { throw new Error("read-only verification accessed durable storage"); } });
  const hub = new PushHub({ storage }, {});
  hub.syncTradingCommit = hub.dispatchEvents = hub.persistImmutableEvent = () => {
    throw new Error("read-only verification attempted a notification mutation");
  };
  return hub;
}

test("verify-commit authenticates exact SHA through GitHub only, without touching storage or delivery", async () => {
  const original = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push(String(url));
    assert.equal(new URL(url).hostname, "api.github.com");
    assert.equal(options.headers.authorization, `Bearer ${token}`);
    assert.equal(options.redirect, "manual");
    return Response.json({ sha, parents: [{ sha: parent }] });
  };
  try {
    const response = await readOnlyHub().fetch(new Request(`https://push.example/verify-commit?sha=${sha}`, {
      headers: { authorization: `Bearer ${token}` },
    }));
    const result = await response.json();
    assert.equal(response.status, 200);
    assert.deepEqual(result, { ok: true, sha, head_sha: sha, parent_sha: parent, authenticated: true, read_only: true });
    assert.equal(calls.length, 2);
    assert.equal(JSON.stringify(result).includes(token), false);
  } finally { globalThis.fetch = original; }
});

test("verify-commit requires Bearer and its failures remain read-only with allowlisted codes", async () => {
  const original = globalThis.fetch;
  let calls = 0;
  globalThis.fetch = async () => { calls += 1; return new Response("secret upstream response", { status: 403 }); };
  try {
    const absent = await readOnlyHub().fetch(new Request(`https://push.example/verify-commit?sha=${sha}`));
    assert.equal(absent.status, 401);
    assert.deepEqual(await absent.json(), { ok: false, error_code: "GITHUB_AUTH_REQUIRED" });
    const malformed = await readOnlyHub().fetch(new Request(`https://push.example/verify-commit?sha=${sha}`, {
      headers: { authorization: `Basic ${token}` },
    }));
    assert.equal(malformed.status, 400);
    assert.deepEqual(await malformed.json(), { ok: false, error_code: "INVALID_GITHUB_AUTHORIZATION" });
    assert.equal(calls, 0);
    const rejected = await readOnlyHub().fetch(new Request(`https://push.example/verify-commit?sha=${sha}`, {
      headers: { authorization: `Bearer ${token}` },
    }));
    assert.equal(rejected.status, 403);
    assert.deepEqual(await rejected.json(), { ok: false, error_code: "GITHUB_HTTP_403" });
  } finally { globalThis.fetch = original; }
});

test("GitHub timeout, runtime fetch and JSON failures get separate safe diagnostic codes", async () => {
  const original = globalThis.fetch;
  const url = "https://api.github.com/repos/Maja2711/briefrooms-site/commits/main";
  try {
    for (const [name, code] of [["TimeoutError", "GITHUB_TIMEOUT"], ["TypeError", "GITHUB_FETCH_FAILED"]]) {
      globalThis.fetch = async () => { throw Object.assign(new Error(`unsafe ${token}`), { name }); };
      await assert.rejects(githubJson(url, { githubToken: token }), (error) => {
        assert.equal(commitSyncErrorCode(error), code);
        assert.equal(error.message.includes(token), false);
        return true;
      });
    }
    globalThis.fetch = async () => new Response(`unsafe ${token}`, { status: 200 });
    await assert.rejects(githubJson(url, { githubToken: token }), (error) => commitSyncErrorCode(error) === "GITHUB_JSON_INVALID");
    assert.equal(commitSyncErrorCode(new Error(`unknown ${token}`)), "SYNC_INTERNAL_ERROR");
    assert.equal(commitSyncErrorCode(new Error(`github_http_403:https://example.test/?secret=${token}`)), "GITHUB_HTTP_403");
  } finally { globalThis.fetch = original; }
});

test("authenticated GitHub fetch works with workerd modes and rejects redirects without following or reading their bodies", async () => {
  const original = globalThis.fetch;
  const url = "https://api.github.com/repos/Maja2711/briefrooms-site/commits/main";
  try {
    for (const status of [200, 302]) {
      const calls = [];
      let bodyReads = 0;
      globalThis.fetch = async (target, options) => {
        // Model the runtime boundary Node's fetch does not enforce.
        if (!["follow", "manual"].includes(options.redirect)) throw new TypeError("Invalid redirect value");
        calls.push(String(target));
        assert.equal(options.redirect, "manual");
        assert.equal(options.headers.authorization, `Bearer ${token}`);
        return status === 200 ? Response.json({ sha }) : {
          status: 302, ok: false,
          headers: new Headers({ location: "https://untrusted.example/redirect" }),
          json: () => { bodyReads += 1; throw new Error(`unsafe ${token}`); },
        };
      };
      if (status === 200) {
        assert.deepEqual(await githubJson(url, { githubToken: token }), { sha });
      } else {
        await assert.rejects(githubJson(url, { githubToken: token }), (error) => {
          assert.equal(commitSyncErrorCode(error), "GITHUB_REDIRECT_REJECTED");
          assert.equal(error.message, "GITHUB_REDIRECT_REJECTED");
          return true;
        });
      }
      assert.deepEqual(calls, [url]);
      assert.equal(bodyReads, 0);
    }
  } finally { globalThis.fetch = original; }
});

test("both sync routes persist only safe error diagnostics while retaining the preceding successful receipt", async () => {
  const originalWarn = console.warn;
  const logs = [];
  console.warn = (...args) => logs.push(JSON.stringify(args));
  try {
    for (const path of ["/sync-trading", "/sync-daily"]) {
      const storage = new Storage();
      const previous = { last_commit_sync_sha: parent, last_commit_sync_at: "2026-10-21T07:00:00Z", last_commit_sync_status: "NO_TRANSITION" };
      await storage.put("stats", previous);
      const hub = new PushHub({ storage }, {});
      hub.syncTradingCommit = async () => { throw new Error(`github_http_403:unsafe_token=${token}`); };
      const response = await hub.fetch(new Request(`https://push.example${path}`, {
        method: "POST", headers: { authorization: `Bearer ${token}` }, body: JSON.stringify({ sha, engines: ["daily"] }),
      }));
      assert.equal(response.status, 403);
      assert.deepEqual(await response.json(), { ok: false, error_code: "GITHUB_HTTP_403" });
      const stats = await storage.get("stats");
      assert.equal(stats.last_commit_sync_sha, previous.last_commit_sync_sha);
      assert.equal(stats.last_commit_sync_at, previous.last_commit_sync_at);
      assert.equal(stats.last_commit_sync_status, previous.last_commit_sync_status);
      assert.equal(stats.last_commit_sync_error_code, "GITHUB_HTTP_403");
      assert.ok(Number.isFinite(Date.parse(stats.last_commit_sync_error_at)));
      assert.equal(JSON.stringify([...storage.map]).includes(token), false);
      const health = await (await hub.fetch(new Request("https://push.example/health"))).json();
      assert.equal(health.last_commit_sync_error_code, "GITHUB_HTTP_403");
      assert.equal(health.last_commit_sync_sha, parent);
    }
    assert.equal(logs.some((line) => line.includes(token)), false);
  } finally { console.warn = originalWarn; }
});
