// Scriptorium — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
// Non-text items: each reader's markers, the zip writer (read back by Python's zipfile), the missing list, the fetchers' address checks.
import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { spawnSync } from "node:child_process";
const require = createRequire(import.meta.url);
const C = require("../common.js"), A = require("../attachments.js");
const chatgpt = require("../sites/chatgpt.js"), claude = require("../sites/claude.js"), grok = require("../sites/grok.js");
const AT = "2026-11-23T10:00:00Z";

test("chatgpt: an upload in a part and a PDF only in metadata, and a tool-message image lands on the next assistant message", () => {
  const r = { current_node: "n5", mapping: {
    n1: { parent: null, message: { id: "m1", author: { role: "user" }, content: { content_type: "multimodal_text", parts: [
      { content_type: "image_asset_pointer", asset_pointer: "file-service://file-AAA", size_bytes: 10, width: 4, height: 3 }, "look at this"] },
      metadata: { attachments: [{ id: "file-AAA", name: "cat.png", mime_type: "image/png", size: 10, width: 4, height: 3 }, { id: "file-BBB", name: "plan.pdf", mime_type: "application/pdf", size: 99 }] } } },
    n2: { parent: "n1", message: { id: "m2", author: { role: "assistant" }, recipient: "dalle", content: { content_type: "code", text: "draw()" } } },
    n3: { parent: "n2", message: { id: "m3", author: { role: "tool" }, content: { content_type: "multimodal_text", parts: [{ content_type: "image_asset_pointer", asset_pointer: "sediment://file_000X", mime_type: "image/png", size_bytes: 5, width: 8, height: 8 }] } } },
    n4: { parent: "n3", message: { id: "m4", author: { role: "tool" }, content: { content_type: "text", parts: ["ignored"] } } },
    n5: { parent: "n4", message: { id: "m5", author: { role: "assistant" }, recipient: "all", content: { content_type: "text", parts: ["here you go"] } } } } };
  const env = chatgpt.toEnvelope(r, "c1", AT);
  assert.equal(env.complete, true);
  assert.equal(env.messages[0].text, "[image: uploaded · cat.png]\nlook at this\n[file: plan.pdf · application/pdf]");
  assert.deepEqual(env.messages[0].attachments.map((a) => [a.id, a.kind, a.origin, a.name, a.pointer]),
    [["file-AAA", "image", "uploaded", "cat.png", "file-service://file-AAA"], ["file-BBB", "file", "uploaded", "plan.pdf", null]]);
  assert.equal(env.messages[1].text, "[image: generated · 1]\nhere you go");
  assert.deepEqual(env.messages[1].attachments[0], { id: "file_000X", kind: "image", origin: "generated", name: null, mime: "image/png", size: 5, width: 8, height: 8, pointer: "sediment://file_000X" });
  assert.equal(env.messages.length, 2);
});

test("claude: files[] and image content are marked; a files-only message is kept", () => {
  const P = "00000000-0000-4000-8000-000000000000";
  const r = { name: "t", current_leaf_message_uuid: "b", chat_messages: [
    { uuid: "a", sender: "human", parent_message_uuid: P, text: "see", content: [{ type: "text", text: "see" }],
      files: [{ file_uuid: "f0", file_name: "t.png", file_kind: "image", media_type: "image/png", preview_url: "/api/f0/preview" },
              { file_uuid: "f1", file_name: "shot.png", file_kind: "image", media_type: "image/png", size_bytes: 7, preview_url: "/api/f1/preview", original_asset: { url: "/api/f1/orig" } },
              { file_uuid: "f2", file_name: "data.csv", file_kind: "document", media_type: "text/csv", size_bytes: 3 }] },
    { uuid: "b", sender: "assistant", parent_message_uuid: "a", content: [{ type: "image", source: { type: "base64", media_type: "image/jpeg" } }] }] };
  const env = claude.toEnvelope(r, "x", AT);
  assert.equal(env.messages[0].text, "[image: uploaded · t.png]\n[image: uploaded · shot.png]\n[file: data.csv · text/csv]\nsee");
  assert.equal(env.messages[0].attachments[0].pointer, null);   // preview only: a thumbnail is never the original
  assert.equal(env.messages[0].attachments[1].pointer, "/api/f1/orig");
  assert.equal(env.messages[0].attachments[2].pointer, null);
  assert.equal(env.messages[1].text, "[image: generated · 1]");
});

test("grok: only what the response shows is marked", () => {
  const r = { conversation: { id: "g" }, responses: [{ response: { _id: "a", message: "q", sender: "human", file_attachments: [{ id: "x1", name: "n.txt", mime_type: "text/plain" }] } }] };
  const env = grok.toEnvelope(r, "g", AT);
  assert.equal(env.messages[0].text, "q\n[file: n.txt · text/plain]");
  assert.equal(env.messages[0].attachments[0].id, "x1");
  assert.equal(grok.toEnvelope({ conversation: { id: "g" }, responses: [{ response: { _id: "a", message: "q", sender: "human" } }] }, "g", AT).messages[0].attachments, undefined);
});

test("zip: Python's zipfile opens what the writer makes, and the CRCs match", () => {
  const files = [{ name: "attachments.json", data: new TextEncoder().encode("[]") }, { name: "files/m1-1-é.png", data: Uint8Array.from([0, 1, 2, 255, 254]) }, { name: "files/empty", data: new Uint8Array(0) }];
  const zip = Buffer.concat(A.zipParts(files));
  const py = `import sys,zipfile,io,json,zlib
z=zipfile.ZipFile(io.BytesIO(sys.stdin.buffer.read()))
assert z.testzip() is None
print(json.dumps([[i.filename,i.compress_type,i.CRC,z.read(i).hex()] for i in z.infolist()]))`;
  const out = spawnSync("python3", ["-I", "-c", py], { input: zip, encoding: "utf8" });
  assert.equal(out.status, 0, out.stderr);
  const got = JSON.parse(out.stdout);
  assert.deepEqual(got.map((g) => [g[0], g[1], g[3]]), [["attachments.json", 0, "5b5d"], ["files/m1-1-é.png", 0, "000102fffe"], ["files/empty", 0, ""]]);
  assert.equal(got[0][2], A.crc32(files[0].data));
  assert.equal(A.crc32(new TextEncoder().encode("123456789")), 0xCBF43926);
});

test("names: the zip, the files inside, and base64 round trip", () => {
  assert.equal(A.zipName("chatgpt", "a/b"), "capture-chatgpt-a%2Fb-attachments.zip");
  const e = (name, mime, messageId = "m1", n = 2) => ({ messageId, n, item: { name, mime, kind: "image" } });
  assert.equal(A.entryName(e("../../x y.png", "image/png")), "files/m1-2-_.._x y.png");
  assert.equal(A.entryName(e(null, "image/png")), "files/m1-2-image.png");
  assert.equal(A.entryName(e("a/b:c", null, "m/9")), "files/m_9-2-a_b_c");
  const b = Uint8Array.from({ length: 70000 }, (_, i) => i % 251);
  assert.deepEqual(A.fromBase64(A.toBase64(b)), b);
});

test("collect: one at a time, a failed fetch is listed missing with its reason, and Save is blocked only if none worked", async () => {
  const env = { messages: [{ id: "m1", attachments: [{ kind: "image", origin: "uploaded", name: "a.png", mime: "image/png" }, { kind: "file", origin: "uploaded", name: "b.pdf", mime: "application/pdf" }] }] };
  const entries = A.listItems(env);
  assert.deepEqual(entries.map((x) => x.marker), ["[image: uploaded · a.png]", "[file: b.pdf · application/pdf]"]);
  let live = 0, peak = 0;
  const seen = [];
  const results = await A.collect(entries, async (en) => {
    peak = Math.max(peak, ++live); await new Promise((r) => setImmediate(r)); live--;
    if (en.n === 2) throw new Error("answered 404");
    return { bytes: Uint8Array.from([1, 2, 3]), mime: "image/png" };
  }, (d, t) => seen.push(d + "/" + t));
  assert.equal(peak, 1);
  assert.deepEqual(seen, ["0/2", "1/2", "1/2", "2/2"]);
  assert.deepEqual(results.map((r) => r.status), ["saved", "missing"]);
  assert.equal(A.anySaved(results), true);
  const m = A.manifest(results);
  assert.deepEqual([m[0].status, m[0].size, m[0].file, m[1].status, m[1].reason, m[1].file], ["saved", 3, "files/m1-1-a.png", "missing", "answered 404", null]);
  assert.equal(A.anySaved(await A.collect(entries, async () => { throw new Error("no"); })), false);
});

test("fetchers: only this chat's addresses; chatgpt asks the download endpoint for the file id, then fetches what it returns", async () => {
  const calls = [];
  const fetch = async (url, init) => {
    calls.push([url, init && init.credentials]);
    const ok = (body) => ({ ok: true, json: async () => body, arrayBuffer: async () => Uint8Array.from([9, 9]).buffer, headers: { get: () => "image/png; x=1" } });
    if (url === "/api/auth/session") return ok({ accessToken: "T" });
    if (url.startsWith("/backend-api/files/download/")) return ok({ download_url: "https://files.example.net/signed?x=1" });
    return ok({});
  };
  const got = await chatgpt.fetchAttachment({ id: "file-AAA", mime: "image/png" }, "c1", { fetch, origin: "https://chatgpt.com" });
  assert.deepEqual([...got.bytes], [9, 9]);
  assert.equal(got.mime, "image/png");
  assert.equal(calls[1][0], "/backend-api/files/download/file-AAA?conversation_id=c1&inline=false");
  assert.deepEqual(calls[2], ["https://files.example.net/signed?x=1", "omit"]);
  await assert.rejects(chatgpt.fetchAttachment({ id: null }, "c1", { fetch, origin: "https://chatgpt.com" }), /no file id/);
  await assert.rejects(claude.fetchAttachment({ pointer: "https://evil.example/x" }, "c", { fetch, origin: "https://claude.ai" }), /not on claude.ai/);
  await assert.rejects(claude.fetchAttachment({ pointer: null }, "c", { fetch, origin: "https://claude.ai" }), /no original/);
  const c = await claude.fetchAttachment({ pointer: "/api/f1/orig" }, "c", { fetch, origin: "https://claude.ai" });
  assert.deepEqual([...c.bytes], [9, 9]);
  await assert.rejects(grok.fetchAttachment(), /cannot be saved yet/);
});

test("chatgpt: a PDF upload on a message with empty parts is kept", () => {
  const r = { current_node: "n1", mapping: { n1: { parent: null, message: { id: "m1", author: { role: "user" }, content: { content_type: "text", parts: [] },
    metadata: { attachments: [{ id: "file-P", name: "plan.pdf", mime_type: "application/pdf", size: 9 }] } } } } };
  const env = chatgpt.toEnvelope(r, "c1", AT);
  assert.equal(env.messages.length, 1);
  assert.equal(env.messages[0].text, "[file: plan.pdf · application/pdf]");
  assert.equal(env.messages[0].attachments[0].id, "file-P");
});

test("chatgpt: a branch ending at a generated image keeps it", () => {
  const r = { current_node: "n2", mapping: {
    n1: { parent: null, message: { id: "m1", author: { role: "user" }, content: { content_type: "text", parts: ["draw"] } } },
    n2: { parent: "n1", message: { id: "m2", author: { role: "tool" }, content: { content_type: "multimodal_text", parts: [{ content_type: "image_asset_pointer", asset_pointer: "sediment://file_000Z", mime_type: "image/png" }] } } } } };
  const env = chatgpt.toEnvelope(r, "c1", AT);
  assert.equal(env.messages.length, 2);
  assert.equal(env.messages[1].role, "assistant");
  assert.equal(env.messages[1].attachments[0].id, "file_000Z");
});

test("chatgpt: a null or empty download_url is refused, never fetched", async () => {
  for (const url of [null, ""]) {
    const calls = [];
    const fetch = async (u) => { calls.push(u); const ok = (b) => ({ ok: true, json: async () => b, arrayBuffer: async () => new ArrayBuffer(1), headers: { get: () => "text/html" } });
      return u === "/api/auth/session" ? ok({ accessToken: "T" }) : ok({ download_url: url }); };
    await assert.rejects(chatgpt.fetchAttachment({ id: "file-A" }, "c1", { fetch, origin: "https://chatgpt.com" }), /no download address/);
    assert.equal(calls.length, 2);
  }
});

test("pull: bytes come in bounded chunks and are rebuilt whole; a short or oversize item is refused", async () => {
  const A = require("../attachments.js");
  const data = Uint8Array.from({ length: 10 }, (_, i) => i);
  const mk = (size) => async (m) => m.type === "scriptorium-fetch-attachment" ? { ok: true, size, chunk: 4, mime: "x/y" }
    : { ok: true, base64: A.toBase64(data.subarray(m.index * 4, m.index * 4 + 4)) };
  const got = await A.pull(mk(10), { type: "scriptorium-fetch-attachment" });
  assert.deepEqual(got.bytes, data);
  await assert.rejects(A.pull(mk(9), { type: "scriptorium-fetch-attachment" }), /larger/);
  await assert.rejects(A.pull(mk(12), { type: "scriptorium-fetch-attachment" }), /incomplete/);
});

test("two attachments whose names clean to the same text get distinct zip paths", async () => {
  const A = (await import("node:module")).createRequire(import.meta.url)("../attachments.js");
  const mk = (mid) => ({ status: "saved", bytes: new Uint8Array([1]), entry: { messageId: mid, n: 1, marker: "", item: { name: "photo.png", kind: "image", mime: "image/png" } } });
  const rs = [mk("a/b"), mk("a_b")];
  const where = A.paths(rs);
  assert.notEqual(where.get(rs[0]), where.get(rs[1]));
  const files = A.manifest(rs, where).map((x) => x.file);
  assert.equal(new Set(files).size, 2);
});

test("a generated image stays with its own turn, never the next answer", () => {
  const G = createRequire(import.meta.url)("../sites/chatgpt.js");
  const node = (id, parent, role, content, extra = {}) => ({ id, parent, message: Object.assign({ id, author: { role }, content, create_time: 1 }, extra) });
  const mapping = {
    r: { id: "r", parent: null, message: null },
    u1: node("u1", "r", "user", { content_type: "text", parts: ["Draw a cat"] }),
    t1: node("t1", "u1", "tool", { content_type: "multimodal_text", parts: [{ content_type: "image_asset_pointer", asset_pointer: "sediment://file_1", mime_type: "image/png" }] }),
    u2: node("u2", "t1", "user", { content_type: "text", parts: ["What is 2+2?"] }),
    a2: node("a2", "u2", "assistant", { content_type: "text", parts: ["4"] }, { recipient: "all" }),
  };
  const env = (G.toEnvelope || G.chatgpt.toEnvelope)({ id: "c", title: "t", current_node: "a2", mapping }, "c", "2026-11-23T00:00:00Z");
  const four = env.messages.find((m) => m.text.includes("4") && m.role === "assistant");
  assert.ok(four, "the answer 4 is there");
  assert.ok(!/image/i.test(four.text), "the cat image is not attached to the 2+2 answer");
  const idx = env.messages.indexOf(four), before = env.messages.slice(0, idx);
  assert.ok(before.some((m) => /image/i.test(m.text)), "the image sits before the next question");
});
