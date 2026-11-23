// Scriptorium — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
// claude.ai reader. toEnvelope is pure (tested); fetchConversation asks this one conversation from the logged-in page.
(function (root, factory) {
  const common = typeof module === "object" && module.exports ? require("../common.js") : root.Scriptorium.common;
  const api = factory(common);
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.Scriptorium.sites.claude = api;
})(globalThis, function (C) {
  const ZERO = "00000000-0000-4000-8000-000000000000";   // parent_message_uuid of a first message

  const isObj = (v) => v !== null && typeof v === "object" && !Array.isArray(v);
  const isImage = (kind, mime) => kind === "image" || /^image\//.test(mime || "");

  // A message as pieces: the files and pasted attachments first (they come before the words), then the content in order.
  function pieces(m) {
    const origin = m.sender === "human" ? "uploaded" : "generated";
    const out = [], seen = new Set();
    for (const f of Array.isArray(m.files) ? m.files : []) {
      if (!isObj(f)) continue;
      const asset = isObj(f.original_asset) ? f.original_asset.url : f.original_asset;
      if (typeof f.file_uuid === "string") seen.add(f.file_uuid);
      out.push(C.makeItem({ id: f.file_uuid, kind: isImage(f.file_kind, f.media_type) ? "image" : "file", origin, name: f.file_name, mime: f.media_type, size: f.size_bytes,
        width: isObj(f.original_asset) ? f.original_asset.image_width : null, height: isObj(f.original_asset) ? f.original_asset.image_height : null,
        pointer: typeof asset === "string" && asset ? asset : null }));   // the original only: a preview_url is a thumbnail, never saved as the file
    }
    // shortcut: pasted-text attachments keep only their extract on the site; they are marked, and listed as missing when saving
    for (const a of Array.isArray(m.attachments) ? m.attachments : []) {
      if (isObj(a)) out.push(C.makeItem({ id: a.id, kind: "file", origin, name: a.file_name, mime: a.file_type, size: a.file_size }));
    }
    const blocks = Array.isArray(m.content) ? m.content.filter(isObj) : [];
    const words = blocks.filter((b) => b.type === "text" && typeof b.text === "string").map((b) => b.text).join("\n");
    const useBlocks = words.trim() !== "";
    if (!useBlocks && typeof m.text === "string") out.push(m.text);
    for (const b of blocks) {
      if (b.type === "text" && typeof b.text === "string") { if (useBlocks) out.push(b.text); }
      else if (b.type === "image" && !(typeof b.file_uuid === "string" && seen.has(b.file_uuid))) {
        const src = isObj(b.source) ? b.source : {};
        out.push(C.makeItem({ id: b.file_uuid, kind: "image", origin, mime: src.media_type, pointer: src.type === "url" ? src.url : null }));
      }
    }
    return out;
  }

  // siteResponse: {uuid, name, current_leaf_message_uuid, chat_messages: [{uuid, sender, parent_message_uuid, content|text, created_at}]}
  function toEnvelope(resp, conversationId, exportedAt) {
    const site = "claude";
    if (!resp || typeof resp !== "object" || !Array.isArray(resp.chat_messages)) return C.changed(site, conversationId, exportedAt, resp, "no chat_messages");
    const byId = new Map();
    for (const m of resp.chat_messages) {
      if (!m || typeof m !== "object" || typeof m.uuid !== "string" || !m.uuid || typeof m.sender !== "string") return C.changed(site, conversationId, exportedAt, resp, "unexpected message");
      byId.set(m.uuid, m);
    }
    const leaf = resp.current_leaf_message_uuid;
    if (typeof leaf !== "string" || !byId.has(leaf)) {
      return resp.chat_messages.length === 0
        ? C.envelope({ site, id: conversationId, title: resp.name, exportedAt, complete: false, reason: "nothing to save: no messages found", raw: resp })
        : C.changed(site, conversationId, exportedAt, resp, "no current leaf");
    }
    const { path, top } = C.walkBranch(byId, leaf, (id) => byId.get(id).parent_message_uuid, (p) => p === ZERO);
    const messages = [], branch = [];
    let streaming = false;
    for (const id of path) {
      const m = byId.get(id);
      if (m.streaming === true || m.status === "in_progress" || m.status === "streaming") streaming = true;   // shortcut: field names unverified live
      const role = m.sender === "human" ? "user" : m.sender === "assistant" ? "assistant" : null;
      const r = C.renderPieces(pieces(m));
      if (!role || !r.text.trim()) continue;
      messages.push(C.message(id, role, r.text, typeof m.created_at === "string" ? m.created_at : null, r.items));
      branch.push(id);
    }
    const reason = !top ? "the earliest turns of this chat are missing" : streaming ? "this chat is still being written; wait for it to finish"
      : messages.length === 0 ? "nothing to save: no messages found" : null;
    return C.envelope({ site, id: conversationId, title: resp.name, exportedAt, complete: !reason, reason, branch, messages, raw: resp });
  }

  async function getJson(url) {
    const r = await fetch(url, { credentials: "include", redirect: "error", headers: { Accept: "application/json" } });
    if (!r.ok) throw new Error("claude.ai answered " + r.status + " for " + new URL(url, location.href).pathname.replace(/[0-9a-f-]{36}/g, ":id"));
    return r.json();
  }
  // Only the open conversation is asked for. The organisations call finds which account owns it; no conversation list is read.
  async function fetchConversation(id) {
    const orgs = await getJson("/api/organizations");
    if (!Array.isArray(orgs) || !orgs.length) throw new Error("claude.ai gave no organisation; are you logged in?");
    let lastErr;
    for (const o of orgs) {
      if (!o || typeof o.uuid !== "string") continue;
      try {
        return await getJson("/api/organizations/" + encodeURIComponent(o.uuid) + "/chat_conversations/" + encodeURIComponent(id) +
          "?tree=True&rendering_mode=messages&render_all_tools=true");
      } catch (e) { lastErr = e; }
    }
    throw lastErr || new Error("claude.ai gave no organisation; are you logged in?");
  }

  // One item's original, from the path the chat itself names, on claude.ai only. shortcut: unverified live.
  async function fetchAttachment(item, conversationId, env) {
    const e = env || {};
    const doFetch = e.fetch || ((...a) => globalThis.fetch(...a));
    if (!item || !item.pointer) throw new Error("the site keeps no original for this item");
    const u = C.sameOriginUrl(item.pointer, e.origin || location.origin);
    if (!u) throw new Error("this item's address is not on claude.ai");
    const got = await C.getBytes(doFetch, u.href, { credentials: "include" });
    return { bytes: got.bytes, mime: got.mime || item.mime };
  }

  return { toEnvelope, fetchConversation, fetchAttachment };
});
