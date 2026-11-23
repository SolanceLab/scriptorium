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

const node = (responseId, parentResponseId, sender = "ASSISTANT") => ({ responseId, sender, parentResponseId });
const rsp = (responseId, message, sender, createTime, extra) => Object.assign({ responseId, message, sender, createTime, partial: false }, extra);
test("grok: no rid falls to the newest end of the tree; empty chat is incomplete", () => {
  const nodes = [node("a", null, "human"), node("b", "a"), node("c", "a")];
  const r = { title: "t", nodes, responses: [rsp("a", "q", "human", "2026-11-23T10:00:00Z"), rsp("b", "old", "ASSISTANT", "2026-11-23T10:00:05Z"), rsp("c", "new", "ASSISTANT", "2026-11-23T10:01:00Z")], inflight: [] };
  assert.deepEqual(sites.grok.toEnvelope(r, "g", AT).messages.map((x) => x.text), ["q", "new"]);
  const empty = sites.grok.toEnvelope({ nodes: [], responses: [], inflight: [] }, "g", AT);
  assert.equal(empty.complete, false);
  assert.match(empty.reason, /no messages/);
});
test("grok: inflight replies or a partial response make it incomplete", () => {
  const base = { title: "t", nodes: [node("a", null, "human"), node("b", "a")], responses: [rsp("a", "q", "human", 1), rsp("b", "half", "ASSISTANT", 2)], inflight: [] };
  assert.equal(sites.grok.toEnvelope(base, "g", AT).complete, true);
  const inflight = sites.grok.toEnvelope(Object.assign({}, base, { inflight: [{ responseId: "z" }] }), "g", AT);
  assert.equal(inflight.complete, false);
  assert.equal(inflight.reason, "Grok is still writing a reply");
  const partial = sites.grok.toEnvelope(Object.assign({}, base, { responses: [base.responses[0], rsp("b", "half", "ASSISTANT", 2, { partial: true })] }), "g", AT);
  assert.equal(partial.reason, "Grok is still writing a reply");
});
test("grok: a branch response that was not loaded means earlier turns are missing", () => {
  const r = { nodes: [node("a", null, "human"), node("b", "a")], responses: [rsp("b", "x", "ASSISTANT", 2)], leaf: "b", inflight: [] };
  assert.match(sites.grok.toEnvelope(r, "g", AT).reason, /earliest turns/);
});

// grok fetchConversation: the tree, the rid leaf, loads of the chosen branch only, in batches of 100.
function grokFetch(tree, texts, log) {
  return async (path, init) => {
    log.push([path, init.method, init.body]);
    if (path.includes("/response-node")) return { ok: true, json: async () => tree };
    const ids = JSON.parse(init.body).responseIds;
    return { ok: true, json: async () => ({ responses: ids.map((i) => texts[i]) }) };
  };
}
const gtree = { responseNodes: [node("r1", null, "human"), node("r2", "r1"), node("r3", "r1"), node("r4", "r3", "human")], inflightResponses: [] };
const gtexts = { r1: rsp("r1", "hi", "human", "2026-11-23T10:00:00Z"), r2: rsp("r2", "old", "ASSISTANT", "2026-11-23T10:00:05Z"), r3: rsp("r3", "new", "ASSISTANT", "2026-11-23T10:01:00Z"), r4: rsp("r4", "more", "human", "2026-11-23T10:02:00Z") };
test("grok: the rid of the page address picks the leaf, and only that branch is loaded", async () => {
  const log = [];
  const r = await sites.grok.fetchConversation("g-1", { fetch: grokFetch(gtree, gtexts, log), search: "?rid=r2", title: "My chat - Grok" });
  assert.equal(r.leaf, "r2");
  assert.equal(r.title, "My chat");
  assert.deepEqual(log.map((c) => c[0]), ["/rest/app-chat/conversations/g-1/response-node?includeThreads=true", "/rest/app-chat/conversations/g-1/load-responses"]);
  assert.deepEqual([log[0][1], log[1][1]], ["GET", "POST"]);
  assert.deepEqual(JSON.parse(log[1][2]).responseIds, ["r1", "r2"]);
  assert.deepEqual(sites.grok.toEnvelope(r, "g-1", AT).messages.map((x) => x.text), ["hi", "old"]);
});
test("grok: no rid takes the newest childless node by createTime", async () => {
  const log = [];
  const r = await sites.grok.fetchConversation("g-1", { fetch: grokFetch(gtree, gtexts, log), search: "" });
  assert.equal(r.leaf, "r4");
  assert.deepEqual(sites.grok.toEnvelope(r, "g-1", AT).messages.map((x) => x.text), ["hi", "new", "more"]);
});
test("grok: more than 100 ids are loaded in batches of 100", async () => {
  const nodes = [node("n0", null, "human")], texts = { n0: rsp("n0", "m0", "human", 0) };
  for (let i = 1; i < 230; i++) { nodes.push(node("n" + i, "n" + (i - 1))); texts["n" + i] = rsp("n" + i, "m" + i, "ASSISTANT", i); }
  const log = [];
  const r = await sites.grok.fetchConversation("g-1", { fetch: grokFetch({ responseNodes: nodes, inflightResponses: [] }, texts, log), search: "?rid=n229" });
  assert.deepEqual(log.slice(1).map((c) => JSON.parse(c[2]).responseIds.length), [100, 100, 30]);
  assert.equal(sites.grok.toEnvelope(r, "g-1", AT).messages.length, 230);
});
test("grok: a failed request ends incomplete with a reason", async () => {
  const r = await sites.grok.fetchConversation("g-1", { fetch: async () => ({ ok: false, status: 500 }), search: "" });
  const env = sites.grok.toEnvelope(r, "g-1", AT);
  assert.equal(env.complete, false);
  assert.match(env.reason, /answered 500/);
  assert.doesNotMatch(env.reason, /unverified/);
});
test("grok fetchConversation: the default fetch is called on globalThis, not detached", async () => {
  const real = globalThis.fetch;
  globalThis.fetch = function () { if (this !== globalThis) throw new TypeError("Illegal invocation"); return Promise.resolve({ ok: true, json: async () => ({ responseNodes: [], inflightResponses: [] }) }); };
  try {
    const r = await sites.grok.fetchConversation("g-1", { search: "" });
    assert.ok(Array.isArray(r.responses));
  } finally { globalThis.fetch = real; }
});

test("grok: no rid and a leaf missing from the reply is incomplete, not an older branch", async () => {
  const texts = { r1: gtexts.r1, r2: gtexts.r2 };   // r4 (the newest end) is left out of the reply
  const tree = { responseNodes: [node("r1", null, "human"), node("r2", "r1"), node("r4", "r1")], inflightResponses: [] };
  const r = await sites.grok.fetchConversation("g-1", { fetch: grokFetch(tree, { r1: texts.r1, r2: texts.r2 }, []), search: "" });
  assert.match(r.unavailable, /texts of this chat/);   // the helper answers an absent id with undefined
  assert.equal(sites.grok.toEnvelope(r, "g-1", AT).complete, false);
});
test("grok: a response with no message and no attachments is incomplete; empty and attachment-only turns are kept", () => {
  const mk = (extra) => ({ nodes: [node("a", null, "human"), node("b", "a")], responses: [rsp("a", "q", "human", 1), Object.assign({ responseId: "b", sender: "ASSISTANT", createTime: 2 }, extra)], inflight: [] });
  assert.equal(sites.grok.toEnvelope(mk({}), "g", AT).complete, false);
  assert.equal(sites.grok.toEnvelope(mk({ message: "" }), "g", AT).complete, true);
  assert.equal(sites.grok.toEnvelope(mk({ generatedImageUrls: ["https://x.example/i.png"] }), "g", AT).complete, true);
});

test("grok: streaming reply is incomplete; image-only reply kept; file names never borrowed", () => {
  const G = createRequire(import.meta.url)("../sites/grok.js");
  const toEnv = G.toEnvelope || (G.grok && G.grok.toEnvelope);
  const nodes = [{ responseId: "u1", sender: "human" }, { responseId: "a1", sender: "assistant", parentResponseId: "u1" }];
  const base = (a1) => ({ title: "t", nodes, inflight: [], responses: [{ responseId: "u1", sender: "human", message: "hi", createTime: "2026-11-23T00:00:00Z" }, Object.assign({ responseId: "a1", sender: "assistant", parentResponseId: "u1", createTime: "2026-11-23T00:00:01Z" }, a1)] });
  assert.equal(toEnv(base({ message: "half", streaming: true }), "c", "2026-11-23T00:00:02Z").complete, false);
  const img = toEnv(base({ message: "", imageAttachments: [{ id: "i1", url: "https://grok.com/x.png" }] }), "c", "2026-11-23T00:00:02Z");
  assert.equal(img.complete, true);
  assert.ok(img.messages.some((m) => m.role === "assistant" && /image/i.test(m.text)), "image-only reply kept");
  const f = toEnv(base({ message: "files", fileAttachments: [{ id: "a" }, { id: "b" }], fileAttachmentsMetadata: [{ fileMetadataId: "b", fileName: "b.pdf", mimeType: "application/pdf" }] }), "c", "2026-11-23T00:00:02Z");
  const names = f.messages.find((m) => m.role === "assistant").attachments.map((x) => x.name);
  assert.equal(names.filter((n) => n === "b.pdf").length, 1, "only file b is called b.pdf");
});

test("grok: the first message's parent outside the tree is the start, not missing history", () => {
  const G = createRequire(import.meta.url)("../sites/grok.js");
  const toEnv = G.toEnvelope || (G.grok && G.grok.toEnvelope);
  const resp = { title: "t", inflight: [], rid: "a1",
    nodes: [{ responseId: "u1", sender: "human", parentResponseId: "hidden-root" }, { responseId: "a1", sender: "assistant", parentResponseId: "u1" }],
    responses: [{ responseId: "u1", sender: "human", message: "hi", parentResponseId: "hidden-root", createTime: "2026-11-23T00:00:00Z" },
      { responseId: "a1", sender: "assistant", message: "hello", parentResponseId: "u1", createTime: "2026-11-23T00:00:01Z" }] };
  const env = toEnv(resp, "c", "2026-11-23T00:00:02Z");
  assert.equal(env.complete, true, env.reason);
  assert.equal(env.messages.length, 2);
});

test("grok: an uploaded file's address comes from its metadata path, and image/jpg makes it an image", () => {
  const G = createRequire(import.meta.url)("../sites/grok.js");
  const toEnv = G.toEnvelope || (G.grok && G.grok.toEnvelope);
  const resp = { title: "t", inflight: [], rid: "u1", nodes: [{ responseId: "u1", sender: "human" }],
    responses: [{ responseId: "u1", sender: "human", message: "look", createTime: "2026-11-23T00:00:00Z", fileAttachments: ["f1"], fileUris: ["f1"],
      fileAttachmentsMetadata: [{ fileMetadataId: "f1", fileMimeType: "image/jpg", fileName: "IMG_1.jpg", fileUri: "users/u9/f1/content" }] }] };
  const item = toEnv(resp, "c", "2026-11-23T00:00:01Z").messages[0].attachments[0];
  assert.equal(item.pointer, "https://assets.grok.com/users/u9/f1/content");
  assert.equal(item.kind, "image");
  assert.equal(item.name, "IMG_1.jpg");
});
