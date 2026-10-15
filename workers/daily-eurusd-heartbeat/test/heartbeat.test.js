import assert from "node:assert/strict";
import test from "node:test";
import { dispatchWatchdog } from "../src/index.js";

const ENV = {
  GITHUB_DISPATCH_TOKEN: "test-token",
  GITHUB_REPOSITORY: "Maja2711/briefrooms-site",
  WATCHDOG_WORKFLOW: "daily-eurusd-cycle-watchdog.yml",
  WATCHDOG_REF: "main",
};

test("dispatches only the Daily EURUSD watchdog", async () => {
  const previous = globalThis.fetch;
  let seen;
  globalThis.fetch = async (url, options) => {
    seen = { url: String(url), options };
    return new Response(null, { status: 204 });
  };
  try {
    const result = await dispatchWatchdog(ENV);
    assert.equal(result.ok, true);
    assert.equal(result.module_id, "TR-03");
    assert.equal(result.trading_authority, false);
    assert.match(seen.url, /daily-eurusd-cycle-watchdog\.yml\/dispatches$/);
    assert.deepEqual(JSON.parse(seen.options.body), { ref: "main" });
    assert.equal(seen.options.method, "POST");
  } finally {
    globalThis.fetch = previous;
  }
});

test("fails closed without GitHub dispatch credential", async () => {
  await assert.rejects(
    () => dispatchWatchdog({ ...ENV, GITHUB_DISPATCH_TOKEN: "" }),
    /github_dispatch_token_missing/,
  );
});

test("does not accept a successful-looking non-204 GitHub response", async () => {
  const previous = globalThis.fetch;
  globalThis.fetch = async () => new Response("forbidden", { status: 403 });
  try {
    await assert.rejects(() => dispatchWatchdog(ENV), /github_dispatch_failed_403/);
  } finally {
    globalThis.fetch = previous;
  }
});
