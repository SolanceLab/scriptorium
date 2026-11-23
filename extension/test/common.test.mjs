// Scriptorium — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
// The filename encoding against the Python encoder (fixtures/filenames.json is made by engine/capture_file.py; the
// Python suite checks the same file), and the one-conversation page check. Run: node --test extension/test
import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
const C = createRequire(import.meta.url)("../common.js");
const names = JSON.parse(readFileSync(new URL("./fixtures/filenames.json", import.meta.url), "utf8"));
const manifest = JSON.parse(readFileSync(new URL("../manifest.json", import.meta.url), "utf8"));

test("filenames match engine/capture_file.py for every fixture id", () => {
  assert.ok(names.length >= 10);
  for (const { site, id, name } of names) assert.equal(C.captureName(site, id), name, JSON.stringify(id));
});

test("only a single conversation page is accepted", () => {
  const ok = (u) => C.parseConversationUrl(u);
  assert.deepEqual(ok("https://claude.ai/chat/abc-1?x=1#y"), { site: "claude", id: "abc-1" });
  assert.deepEqual(ok("https://chatgpt.com/c/69a1"), { site: "chatgpt", id: "69a1" });
  assert.deepEqual(ok("https://grok.com/chat/g1"), { site: "grok", id: "g1" });
  assert.deepEqual(ok("https://grok.com/c/g2/"), { site: "grok", id: "g2" });
  for (const bad of ["https://claude.ai/recents", "https://claude.ai/chat/", "https://claude.ai/chat/a/b", "https://chatgpt.com/", "https://chatgpt.com/g/g-x", "https://chatgpt.com/g/g-x/c/1/extra",
    "https://chatgpt.com/c/1/extra", "http://claude.ai/chat/a", "https://evil.example/chat/a", "https://claude.ai.evil.example/chat/a", "not a url", "https://grok.com/c/%E0%A4%A"]) {
    assert.equal(ok(bad), null, bad);
  }
});

test("manifest holds only the three sites and the three permissions", () => {
  assert.deepEqual([...manifest.host_permissions].sort(), ["https://chatgpt.com/*", "https://claude.ai/*", "https://grok.com/*"]);
  assert.deepEqual([...manifest.permissions].sort(), ["activeTab", "downloads", "scripting"]);
  assert.equal(manifest.manifest_version, 3);
  assert.deepEqual(manifest.background, { service_worker: "background.js" });   // only to open the export window
  assert.equal(manifest.action.default_popup, undefined);
  for (const cs of manifest.content_scripts) assert.deepEqual([...cs.matches].sort(), [...manifest.host_permissions].sort());
});

test("no extension code lists other conversations or calls a remote host", () => {
  const files = ["content.js", "export.js", "background.js", "sites/claude.js", "sites/chatgpt.js", "sites/grok.js"];
  for (const f of files) {
    const src = readFileSync(new URL("../" + f, import.meta.url), "utf8");
    assert.doesNotMatch(src, /chat_conversations_v2|chat_conversations["'?]|conversations\?offset|\/conversations\?/, f);
    assert.doesNotMatch(src, /fetch\(\s*["'`]https?:/, f);
  }
});

test("save: only a complete envelope can be saved; the file is a Blob, not a data: URL", async () => {
  assert.equal(C.canSave(null), false);
  assert.equal(C.canSave({ complete: false }), false);
  assert.equal(C.canSave({ complete: true }), true);
  const env = { complete: true, big: "x".repeat(3 * 1024 * 1024) };
  const b = C.captureBlob(env);
  assert.ok(b instanceof Blob && b.size > 3 * 1024 * 1024 && b.type === "application/json");
  assert.deepEqual(JSON.parse(await b.text()), env);
});

test("captureFilename: only a complete envelope gets the exact capture name", () => {
  assert.equal(C.captureFilename({ complete: false, site: "claude", conversation_id: "a" }), null);
  assert.equal(C.captureFilename({ complete: true, site: "claude", conversation_id: "a/b c" }), C.captureName("claude", "a/b c"));
});

test("export window address round-trips, and only a plain tab id is read back", () => {
  assert.equal(C.exportPagePath(42), "export.html?tab=42");
  assert.equal(C.tabIdFromSearch("?tab=42"), 42);
  for (const bad of ["", "?tab=", "?tab=-1", "?tab=1&x=2", "?tab=abc", "?x=1", "?tab=1e3"]) assert.equal(C.tabIdFromSearch(bad), null, bad);
});

test("downloadOutcome: complete, cancelled, real error, or still going", () => {
  assert.deepEqual(C.downloadOutcome({ state: { current: "complete" } }), { ok: true });
  assert.deepEqual(C.downloadOutcome({ state: { current: "interrupted" }, error: { current: "USER_CANCELED" } }), { error: "Cancelled" });
  assert.deepEqual(C.downloadOutcome({ state: { current: "interrupted" }, error: { current: "FILE_ACCESS_DENIED" } }), { error: "FILE_ACCESS_DENIED" });
  assert.deepEqual(C.downloadOutcome({ state: { current: "interrupted" } }), { error: "interrupted" });
  assert.equal(C.downloadOutcome({ state: { current: "in_progress" } }), null);
  assert.equal(C.downloadOutcome({ filename: {} }), null);
});

test("once runs a revoke exactly once", () => {
  let n = 0; const r = C.once(() => n++);
  assert.equal(r(), true); assert.equal(r(), false); r();
  assert.equal(n, 1);
});

import { createRequire as _cr } from "node:module";
{
  const { test: t } = await import("node:test");
  const assertM = await import("node:assert/strict");
  const common = _cr(import.meta.url)("../common.js");
  t("save filename allows ~ in an id", () => {
    const env = { site: "claude", conversation_id: "a~b", complete: true, messages: [] };
    assertM.default.equal(common.captureFilename(env), "capture-claude-a~b.json");
  });
}

test("ChatGPT chats inside a project or a custom GPT are recognised", () => {
  const parse = C.parseConversationUrl;
  assert.deepEqual(parse("https://chatgpt.com/g/g-p-6912abc-public-ledger/c/6af71691-f6fc-83ec-a904-5489b9502cfc"), { site: "chatgpt", id: "6af71691-f6fc-83ec-a904-5489b9502cfc" });
  assert.deepEqual(parse("https://chatgpt.com/g/g-abc123-my-gpt/c/abc-1"), { site: "chatgpt", id: "abc-1" });
  assert.deepEqual(parse("https://chatgpt.com/c/abc-1"), { site: "chatgpt", id: "abc-1" });
  assert.equal(parse("https://chatgpt.com/g/g-p-6912abc-public-ledger/project"), null);
});

test("scrub: signed query strings, fragments, logins and token keys never reach the saved file", async () => {
  const signed = "https://files.example.net/a/b.png?sig=SECRETSIG&Expires=99#frag";
  const env = { complete: true, site: "chatgpt", conversation_id: "c", pointer: signed, messages: [{ attachments: [{ pointer: signed }] }],
    raw: { deep: [{ url: "https://u:p@x.example/p?token=abc" }], accessToken: "BEARERVALUE", keep: "see https://x.example/q?z=1 in text is prose", n: 3 } };
  const text = await C.captureBlob(env).text();
  for (const bad of ["SECRETSIG", "Expires", "frag", "BEARERVALUE", "accessToken", "token=abc", "u:p@"]) assert.ok(!text.includes(bad), bad);
  const out = JSON.parse(text);
  assert.equal(out.pointer, "https://files.example.net/a/b.png");
  assert.equal(out.raw.deep[0].url, "https://x.example/p");
  assert.equal(out.raw.n, 3);
  assert.equal(env.pointer, signed);   // the copy in memory (which the attachment fetch reads) is untouched
  const A = createRequire(import.meta.url)("../attachments.js");
  const parts = A.archiveParts([{ entry: { messageId: "m", n: 1, marker: "x", item: { name: "n", mime: null, size: 1 } }, status: "missing", reason: "https://h.example/f?sig=ZZZ" }]);
  assert.ok(!Buffer.concat(parts.map((x) => Buffer.from(x))).toString("latin1").includes("ZZZ"));
});

test("getBytes: a credentialed request never follows a redirect; an uncredentialed one is left alone", async () => {
  const seen = [];
  const f = async (u, init) => { seen.push(init); return { ok: true, arrayBuffer: async () => new ArrayBuffer(0), headers: { get: () => null } }; };
  await C.getBytes(f, "https://a.example/x", { credentials: "include" });
  await C.getBytes(f, "https://a.example/x", { credentials: "omit" });
  assert.equal(seen[0].redirect, "error");
  assert.equal(seen[1].redirect, undefined);
});

test("scrub: message text that starts with a link keeps its text; relative signed pointers lose the query", async () => {
  const text = "https://example.com/?q=hello and more words";
  const env = { complete: true, site: "claude", conversation_id: "c", messages: [{ text, attachments: [{ pointer: "/file?sig=RELSIG#x" }] }], raw: { download_url: "/dl/a?sig=RELSIG2", note: "/not/a/url?x=1" } };
  const out = JSON.parse(await C.captureBlob(env).text());
  assert.equal(out.messages[0].text, text);
  assert.equal(out.messages[0].attachments[0].pointer, "/file");
  assert.equal(out.raw.download_url, "/dl/a");
  assert.equal(out.raw.note, "/not/a/url?x=1");
});

test("scrub: a message that is only a link keeps its text; bare signed paths under fileUris lose the query", async () => {
  const text = "https://example.com/search?q=hello#results";
  const env = { complete: true, site: "grok", conversation_id: "c", messages: [{ text }], raw: { fileUris: ["media/a.png?sig=TEST"] } };
  const out = JSON.parse(await C.captureBlob(env).text());
  assert.equal(out.messages[0].text, text);
  assert.deepEqual(out.raw.fileUris, ["media/a.png"]);
});

test("signed links leave every field of the saved file, written text stays as written", async () => {
  const env = { site: "grok", messages: [{ id: "m1", role: "assistant", text: "see https://x.example/a?keep=1",
    attachments: [{ id: "https://assets.grok.com/g/1.png?sig=SECRET&Expires=9", pointer: "https://assets.grok.com/g/1.png?sig=SECRET" }] }],
    raw: { responses: [{ message: "https://x.example/a?keep=1", generatedImageUrls: ["https://assets.grok.com/g/1.png?sig=SECRET&Expires=9"] }] } };
  const out = await C.captureBlob(env).text();
  assert.ok(!out.includes("SECRET"), "no signature left anywhere");
  assert.ok(!out.includes("Expires"), "no expiry left anywhere");
  assert.ok(out.includes("see https://x.example/a?keep=1"), "the message text is untouched");
});

test("a title that is only a link, and links without https: carrying a login, are cleaned", async () => {
  const out = await C.captureBlob({ site: "claude", title: "https://x.example/t?sig=SECRET1", messages: [],
    raw: { thumb: "//user:pw2@cdn.example/a.png?sig=SECRET2", list: ["//cdn.example/b.png?Expires=SECRET3"] } }).text();
  for (const s of ["SECRET1", "SECRET2", "SECRET3", "pw2"]) assert.ok(!out.includes(s), s);
});
