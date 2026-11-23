// Scriptorium — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
// Regression: an export still pending when the window rechecks must not restore its preview or enable Save.
import test from "node:test";
import assert from "node:assert/strict";
import vm from "node:vm";
import { readFileSync } from "node:fs";
const read = (f) => readFileSync(new URL("../" + f, import.meta.url), "utf8");

function el() {
  const cls = new Set();
  return { hidden: false, disabled: false, textContent: "", classList: { toggle: (c, on) => (on ? cls.add(c) : cls.delete(c)) }, handlers: {}, addEventListener(t, fn) { this.handlers[t] = fn; } };
}

test("a stale export reply after a recheck does not restore the preview or enable Save", async () => {
  const els = {};
  const document = { getElementById: (id) => (els[id] ||= el()) };
  let url = "https://claude.ai/chat/A";
  let resolveReply;
  const chrome = {
    tabs: { get: async () => ({ id: 7, url }), sendMessage: () => new Promise((r) => (resolveReply = r)) },
    runtime: { onMessage: { addListener: (fn) => (chrome.recheck = fn) } },
    downloads: { onChanged: { addListener() {}, removeListener() {} } },
  };
  const ctx = { document, chrome, location: { search: "?tab=7" }, window: { addEventListener() {} }, URL, Blob, console };
  ctx.globalThis = ctx;
  vm.createContext(ctx);
  vm.runInContext(read("common.js"), ctx);
  vm.runInContext(read("export.js"), ctx);
  const tick = () => new Promise((r) => setImmediate(r));
  await tick();

  const pending = els.export.handlers.click();   // parks on the pending reply
  await tick();
  url = "https://claude.ai/chat/B";               // the source tab moves on
  chrome.recheck({ type: "scriptorium-recheck", tabId: 7 });
  await tick();
  assert.equal(els.preview.hidden, true);
  resolveReply({ ok: true, envelope: { site: "claude", conversation_id: "A", title: "t", complete: true, messages: [] } });
  await pending;
  await tick();
  assert.equal(els.preview.hidden, true, "stale preview stays hidden");
  assert.equal(els.export.disabled, false, "Export is clickable again after the recheck");
  assert.equal(els.save.disabled, true, "Save stays disabled");
  assert.equal(els.save.hidden, true);
});

// content.js: the export ask names the chat the window parsed; the page must still show it before and after the fetch.
function runContent(href, fetchConversation) {
  let listener;
  const S = { common: null, sites: { claude: { fetchConversation, toEnvelope: (d, id) => ({ site: "claude", conversation_id: id, complete: true, messages: [] }) } }, attachments: { CHUNK: 4 } };
  const location = { href };
  const ctx = { chrome: { runtime: { onMessage: { addListener: (fn) => (listener = fn) } } }, location, URL, console };
  ctx.globalThis = ctx; ctx.Scriptorium = S;
  vm.createContext(ctx);
  vm.runInContext(read("common.js"), ctx);
  S.common = ctx.Scriptorium.common;
  vm.runInContext(read("content.js"), ctx);
  const ask = (msg) => new Promise((r) => { listener(msg, {}, r); });
  return { ask, location };
}
test("content: refuses when the open chat is not the one asked for, before and after fetching", async () => {
  let c = runContent("https://claude.ai/chat/B", async () => ({}));
  const r0 = await c.ask({ type: "scriptorium-export", site: "claude", id: "A" });
  assert.equal(r0.ok, false); assert.equal(r0.error, "a different chat is open now");
  c = runContent("https://claude.ai/chat/A", async () => { c.location.href = "https://claude.ai/chat/B"; return {}; });
  assert.equal((await c.ask({ type: "scriptorium-export", site: "claude", id: "A" })).error, "a different chat is open now");
  c = runContent("https://claude.ai/chat/A", async () => ({}));
  assert.equal((await c.ask({ type: "scriptorium-export", site: "claude", id: "A" })).ok, true);
  assert.equal((await c.ask({ type: "scriptorium-export" })).error, "a different chat is open now");   // an ask that names no chat is refused
});
