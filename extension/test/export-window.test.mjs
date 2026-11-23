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
