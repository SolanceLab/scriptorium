// Scriptorium — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
// chatgpt.com reader: the same mapping/current_node logic as engine/chatgpt_export.py, in JavaScript.
(function (root, factory) {
  const common = typeof module === "object" && module.exports ? require("../common.js") : root.Scriptorium.common;
  const api = factory(common);
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.Scriptorium.sites.chatgpt = api;
})(globalThis, function (C) {
  const isObj = (v) => v !== null && typeof v === "object" && !Array.isArray(v);

  // "sediment://file_00..." or "file-service://file-..." -> the file id the download endpoint takes, or null.
  function fileId(pointer) {
    const m = typeof pointer === "string" ? /^(?:sediment|file-service):\/\/(.+)$/.exec(pointer) : null;
    return m ? m[1] : null;
  }
  const uploadsOf = (meta) => (Array.isArray(meta.attachments) ? meta.attachments.filter(isObj) : []);

  // A message's content as pieces: words, and each non-text part as an item at its place (see common.js renderPieces).
  // `uploads` is the user message's metadata.attachments; one that matches a part gives that part its name.
  function pieces(content, origin, uploads) {
    if (!isObj(content)) content = {};
    if (Array.isArray(content.parts) && content.parts.length || uploads.length) {   // uploads count even when the parts are empty
      const out = [], used = new Set();
      if (!(Array.isArray(content.parts) && content.parts.length) && typeof content.text === "string") out.push(content.text);
      for (const p of Array.isArray(content.parts) ? content.parts : []) {
        if (typeof p === "string") out.push(p);
        else if (isObj(p) && typeof p.text === "string") out.push(p.text);
        else if (isObj(p) && p.content_type === "audio_asset_pointer") out.push("[audio]");   // shortcut: audio is marked, not fetched
        else if (isObj(p) && (p.content_type === "image_asset_pointer" || typeof p.asset_pointer === "string")) {
          const id = fileId(p.asset_pointer), up = uploads.find((u) => id && u.id === id);
          if (up) used.add(up);
          out.push(C.makeItem({
            id, kind: p.content_type === "image_asset_pointer" || /^image\//.test(p.mime_type || (up && up.mime_type) || "") ? "image" : "file", origin,
            name: up && up.name, mime: p.mime_type || (up && up.mime_type), size: p.size_bytes ?? (up && up.size), width: p.width ?? (up && up.width),
            height: p.height ?? (up && up.height), pointer: p.asset_pointer }));
        } else if (p !== null && p !== undefined) out.push("[attachment]");
      }
      for (const u of uploads) {   // uploads that sit in no part (a PDF, a spreadsheet)
        if (used.has(u)) continue;
        out.push(C.makeItem({ id: u.id, kind: /^image\//.test(u.mime_type || "") ? "image" : "file", origin, name: u.name, mime: u.mime_type, size: u.size, width: u.width, height: u.height, pointer: u.library_file_id }));
      }
      return out;
    }
    return typeof content.text === "string" ? [content.text] : [];   // "code" and "execution_output" carry `text`
  }

  // siteResponse: {conversation_id|id, title, current_node, mapping: {node id: {id, parent, message}}}
  function toEnvelope(resp, conversationId, exportedAt) {
    const site = "chatgpt";
    if (!isObj(resp) || !isObj(resp.mapping) || typeof resp.current_node !== "string") return C.changed(site, conversationId, exportedAt, resp, "no mapping or current_node");
    const byId = new Map(Object.entries(resp.mapping));
    for (const node of byId.values()) if (!isObj(node)) return C.changed(site, conversationId, exportedAt, resp, "unexpected node");
    if (!byId.has(resp.current_node)) return C.changed(site, conversationId, exportedAt, resp, "current_node not in mapping");
    const { path, top } = C.walkBranch(byId, resp.current_node, (id) => byId.get(id).parent);
    const messages = [], branch = [];
    let streaming = false, missing = false, pending = [];
    for (const nid of path) {
      const m = byId.get(nid).message;
      if (!isObj(m)) { if (nid !== path[0]) missing = true; continue; }   // only the root node may have no message
      const role = isObj(m.author) ? m.author.role : null;
      const meta = isObj(m.metadata) ? m.metadata : {};
      if (m.status === "in_progress" && role === "assistant") streaming = true;
      if (role === "tool" && isObj(m.content) && m.content.content_type === "multimodal_text") {   // a generated image: it belongs to the next assistant turn
        pending.push(...pieces(m.content, "generated", []).filter((p) => typeof p !== "string"));
        continue;
      }
      if ((role !== "user" && role !== "assistant") || meta.is_visually_hidden_from_conversation) continue;
      if (m.recipient !== undefined && m.recipient !== null && m.recipient !== "all") continue;   // a tool call, not a chat turn
      if (role === "user" && pending.length) {   // a generated image whose turn had no written reply: it stays with that turn, never the next one
        const r0 = C.renderPieces(pending), prev = branch.length ? branch[branch.length - 1] : nid;
        messages.push(C.message("tool-" + prev, "assistant", r0.text, null, r0.items));
        branch.push("tool-" + prev); pending = [];
      }
      const ps = pieces(m.content, role === "user" ? "uploaded" : "generated", role === "user" ? uploadsOf(meta) : []);
      const r = C.renderPieces(role === "assistant" ? pending.concat(ps) : ps);
      if (!r.text.trim()) continue;
      if (role === "assistant") pending = [];
      const mid = typeof m.id === "string" && m.id ? m.id : nid;
      messages.push(C.message(mid, role, r.text, C.isoFromEpoch(m.create_time), r.items));
      branch.push(mid);
    }
    if (pending.length) {   // the branch ends at a generated image: keep it as its own assistant turn
      const r = C.renderPieces(pending), last = path[path.length - 1];
      messages.push(C.message("tool-" + last, "assistant", r.text, null, r.items));
      branch.push("tool-" + last);
    }
    const reason = !top ? "the earliest turns of this chat are missing" : missing ? "part of this chat is missing" : streaming ? "this chat is still being written; wait for it to finish"
      : messages.length === 0 ? "nothing to save: no messages found" : null;
    return C.envelope({ site, id: conversationId, title: resp.title, exportedAt, complete: !reason, reason, branch, messages, raw: resp });
  }

  // Every API address is absolute on chatgpt.com, built from the page's origin (never document.baseURI), and checked before the bearer goes out.
  async function getJson(path, headers, doFetch, origin) {
    const u = new URL(path, origin || location.origin);
    if (u.origin !== "https://chatgpt.com") throw new Error("this is not chatgpt.com; nothing was sent");
    const r = await (doFetch || ((...a) => globalThis.fetch(...a)))(u.href, { credentials: "include", redirect: "error", headers: Object.assign({ Accept: "application/json" }, headers) });
    if (!r.ok) throw new Error("chatgpt.com answered " + r.status + " for " + u.pathname.replace(/[0-9a-f-]{36}/g, ":id"));
    return r.json();
  }
  async function token(doFetch, origin) {
    const session = await getJson("/api/auth/session", undefined, doFetch, origin);
    if (!session || typeof session.accessToken !== "string") throw new Error("chatgpt.com gave no access token; are you logged in?");
    return session.accessToken;
  }
  async function fetchConversation(id) {
    return getJson("/backend-api/conversation/" + encodeURIComponent(id), { Authorization: "Bearer " + await token() });
  }

  // One item's original: ask chatgpt.com for its download address, then fetch that address. Only this chat's own file id is asked for.
  // shortcut: unverified live; a signed address on another host is fetched without credentials, and may need CORS to allow it.
  async function fetchAttachment(item, conversationId, env) {
    const e = env || {};
    const doFetch = e.fetch || ((...a) => globalThis.fetch(...a));
    const origin = e.origin || location.origin;
    if (!item || !item.id) throw new Error("this item has no file id");
    const auth = { Authorization: "Bearer " + await token(doFetch, origin) };
    const meta = await getJson("/backend-api/files/download/" + encodeURIComponent(item.id) + "?conversation_id=" + encodeURIComponent(conversationId) + "&inline=false", auth, doFetch, origin);
    let u;
    const addr = isObj(meta) ? meta.download_url : null;
    try { u = typeof addr === "string" && addr ? new URL(addr, origin) : null; } catch (err) { u = null; }
    if (!u || u.protocol !== "https:") throw new Error("chatgpt.com gave no download address");
    const mine = u.origin === origin;
    const got = await C.getBytes(doFetch, u.href, mine ? { credentials: "include", headers: auth } : { credentials: "omit" });
    return { bytes: got.bytes, mime: got.mime || item.mime };
  }

  return { toEnvelope, fetchConversation, fetchAttachment, fileId };
});
