// Scriptorium — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
// Each site reader's toEnvelope against synthetic responses in the real shapes. Run: node --test extension/test
import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
const require = createRequire(import.meta.url);
const sites = { claude: require("../sites/claude.js"), chatgpt: require("../sites/chatgpt.js"), grok: require("../sites/grok.js") };
const fx = (name) => JSON.parse(readFileSync(new URL("./fixtures/" + name, import.meta.url), "utf8"));
const ids = { claude: "c1-é/2", chatgpt: "69a1-b", grok: "g-1" };
const AT = "2026-11-23T10:00:00Z";

for (const site of Object.keys(sites)) {
  test(site + ": branched chat gives the shown branch, matching the checked-in capture file", () => {
    const env = sites[site].toEnvelope(fx(site + "-branched.response.json"), ids[site], AT);
    assert.deepEqual(env, fx(site + "-branched.capture.json"));
    assert.equal(env.complete, true);
    assert.equal(env.messages.length, 3);
    assert.ok(env.messages[1].text.startsWith("retried answer"));
    assert.equal(env.messages[2].text, "and again");
    assert.equal(env.schema_version, 1);
    assert.equal(env.exported_at, AT);
  });

  test(site + ": unexpected shape is incomplete with the reason, never complete", () => {
    for (const bad of [null, "text", [], {}, { mapping: 3, chat_messages: "x", responses: {} }]) {
      const env = sites[site].toEnvelope(bad, "x1", AT);
      assert.equal(env.complete, false);
      assert.match(env.reason, /this site changed; update the extension/);
    }
  });
}

test("claude: linear chat, empty and identical consecutive turns", () => {
  const P = "00000000-0000-4000-8000-000000000000";
  const r = { name: "t", current_leaf_message_uuid: "d", chat_messages: [
    { uuid: "a", sender: "human", parent_message_uuid: P, text: "same" },
    { uuid: "b", sender: "assistant", parent_message_uuid: "a", content: [{ type: "text", text: "  " }] },
    { uuid: "c", sender: "human", parent_message_uuid: "b", text: "same" },
    { uuid: "d", sender: "assistant", parent_message_uuid: "c", content: [{ type: "text", text: "ok" }] }] };
  const env = sites.claude.toEnvelope(r, "x", AT);
  assert.deepEqual(env.messages.map((m) => [m.role, m.text]), [["user", "same"], ["user", "same"], ["assistant", "ok"]]);
  assert.deepEqual(env.branch, ["a", "c", "d"]);
  assert.equal(env.complete, true);
});

test("claude: missing earliest turns and a streaming turn are incomplete", () => {
  const r = { name: "t", current_leaf_message_uuid: "b", chat_messages: [
    { uuid: "b", sender: "assistant", parent_message_uuid: "gone", text: "late" }] };
  const env = sites.claude.toEnvelope(r, "x", AT);
  assert.equal(env.complete, false);
  assert.match(env.reason, /earliest turns/);
  const s = { name: "t", current_leaf_message_uuid: "b", chat_messages: [
    { uuid: "a", sender: "human", parent_message_uuid: null, text: "q" },
    { uuid: "b", sender: "assistant", parent_message_uuid: "a", text: "partial", status: "in_progress" }] };
  assert.match(sites.claude.toEnvelope(s, "x", AT).reason, /still being written/);
});

test("chatgpt: a loop, a missing parent and an in-progress answer are incomplete", () => {
  const msg = (role, t, extra) => Object.assign({ author: { role }, content: { parts: [t] } }, extra);
  const loop = { current_node: "a", mapping: { a: { parent: "b", message: msg("user", "x") }, b: { parent: "a", message: msg("user", "y") } } };
  assert.equal(sites.chatgpt.toEnvelope(loop, "x", AT).complete, false);
  const orphan = { current_node: "a", mapping: { a: { parent: "zz", message: msg("user", "x") } } };
  assert.match(sites.chatgpt.toEnvelope(orphan, "x", AT).reason, /earliest turns/);
  const live = { current_node: "b", mapping: { a: { parent: null, message: msg("user", "x") }, b: { parent: "a", message: msg("assistant", "par", { status: "in_progress" }) } } };
  assert.match(sites.chatgpt.toEnvelope(live, "x", AT).reason, /still being written/);
});

test("chatgpt: hidden, empty and tool turns are skipped; identical consecutive turns kept", () => {
  const m = (role, t, extra) => ({ message: Object.assign({ author: { role }, content: { parts: [t] } }, extra) });
  const r = { current_node: "e", mapping: {
    a: Object.assign({ parent: null }, m("system", "sys")),
    b: Object.assign({ parent: "a" }, m("user", "same")),
    c: Object.assign({ parent: "b" }, m("user", "same")),
    d: Object.assign({ parent: "c" }, m("assistant", "hidden", { metadata: { is_visually_hidden_from_conversation: true } })),
    e: Object.assign({ parent: "d" }, m("assistant", "   ")) } };
  const env = sites.chatgpt.toEnvelope(r, "x", AT);
  assert.deepEqual(env.messages.map((x) => x.text), ["same", "same"]);
  assert.equal(env.complete, true);
});

test("grok: no leaf falls to the latest message; empty chat is incomplete", () => {
  const r = { conversation: { id: "g", title: "t" }, responses: [
    { response: { _id: "a", message: "q", sender: "human", create_time: 1 } },
    { response: { _id: "b", message: "a", sender: "ASSISTANT", parent_response_id: "a", create_time: 2 } }] };
  assert.deepEqual(sites.grok.toEnvelope(r, "g", AT).messages.map((x) => x.text), ["q", "a"]);
  const empty = sites.grok.toEnvelope({ conversation: { id: "g" }, responses: [] }, "g", AT);
  assert.equal(empty.complete, false);
  assert.match(empty.reason, /no messages/);
});

// grok fetchConversation: candidates newest-first, GET only, fall back to incomplete with a reason.
const grokNames = ["https://grok.com/rest/app-chat/conversations/g-1/responses", "https://grok.com/rest/app-chat/conversations/g-1/load-responses", "https://grok.com/rest/app-chat/conversations", "https://other.com/rest/g-1/x", "https://grok.com/rest/app-chat/conversations/g-1/responses"];
test("grok: candidates are same-origin, id-bearing, newest first, once each, never a list", () => {
  assert.deepEqual(sites.grok.candidateUrls(grokNames, "https://grok.com", "g-1"),
    ["/rest/app-chat/conversations/g-1/responses", "/rest/app-chat/conversations/g-1/load-responses"]);
});
test("grok: tries newest first with GET until one has the expected shape", async () => {
  const calls = [];
  const good = fx("grok-branched.response.json");
  const fetch = async (path, init) => { calls.push([path, init.method]); return path.endsWith("load-responses") ? { ok: true, json: async () => good } : { ok: true, json: async () => ({ nope: 1 }) }; };
  const names = grokNames.slice(0, 2);
  const r = await sites.grok.fetchConversation("g-1", { names, origin: "https://grok.com", fetch });
  assert.equal(r, good);
  assert.deepEqual(calls.map((c) => c[0]), ["/rest/app-chat/conversations/g-1/load-responses"]);
  assert.ok(calls.every((c) => c[1] === "GET"));
});
test("grok: falls to the next candidate on a bad status, and ends incomplete with a reason when none works", async () => {
  const fetch = async () => ({ ok: false, status: 500 });
  const r = await sites.grok.fetchConversation("g-1", { names: grokNames, origin: "https://grok.com", fetch });
  const env = sites.grok.toEnvelope(r, "g-1", AT);
  assert.equal(env.complete, false);
  assert.match(env.reason, /none of the requests/);
  const none = sites.grok.toEnvelope(await sites.grok.fetchConversation("g-1", { names: [], origin: "https://grok.com", fetch }), "g-1", AT);
  assert.equal(none.complete, false);
  assert.match(none.reason, /not loaded/);
});

test("grok fetchConversation: the default fetch is called on globalThis, not detached", async () => {
  const real = globalThis.fetch;
  globalThis.fetch = function () { if (this !== globalThis) throw new TypeError("Illegal invocation"); return Promise.resolve({ ok: true, json: async () => ({ responses: [] }) }); };
  try {
    const r = await sites.grok.fetchConversation("g-1", { names: grokNames, origin: "https://grok.com" });
    assert.ok(Array.isArray(r.responses));
  } finally { globalThis.fetch = real; }
});
