// Scriptorium — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
// chatgpt.com reader: the same mapping/current_node logic as engine/chatgpt_export.py, in JavaScript.
(function (root, factory) {
  const common = typeof module === "object" && module.exports ? require("../common.js") : root.Scriptorium.common;
  const api = factory(common);
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.Scriptorium.sites.chatgpt = api;
})(globalThis, function (C) {
  const isObj = (v) => v !== null && typeof v === "object" && !Array.isArray(v);

  // The words of a message's content; a short placeholder stands for each part that is not text.
  function text(content) {
    if (!isObj(content)) return "";
    if (Array.isArray(content.parts) && content.parts.length) {
      const out = [];
      for (const p of content.parts) {
        if (typeof p === "string") out.push(p);
        else if (isObj(p) && typeof p.text === "string") out.push(p.text);
        else if (isObj(p) && (p.content_type === "image_asset_pointer" || p.content_type === "audio_asset_pointer")) out.push(p.content_type.startsWith("image") ? "[image]" : "[audio]");
        else if (p !== null && p !== undefined) out.push("[attachment]");
      }
      return out.filter((s) => s.trim()).join("\n");
    }
    return typeof content.text === "string" ? content.text : "";   // "code" and "execution_output" carry `text`
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
    let streaming = false;
    for (const nid of path) {
      const m = byId.get(nid).message;
      if (!isObj(m)) continue;
      const role = isObj(m.author) ? m.author.role : null;
      const meta = isObj(m.metadata) ? m.metadata : {};
      if (m.status === "in_progress" && role === "assistant") streaming = true;
      if ((role !== "user" && role !== "assistant") || meta.is_visually_hidden_from_conversation) continue;
      if (m.recipient !== undefined && m.recipient !== null && m.recipient !== "all") continue;   // a tool call, not a chat turn
      const t = text(m.content);
      if (!t.trim()) continue;
      const mid = typeof m.id === "string" && m.id ? m.id : nid;
      messages.push({ id: mid, role, text: t, created_at: C.isoFromEpoch(m.create_time) });
      branch.push(mid);
    }
    const reason = !top ? "the earliest turns of this chat are missing" : streaming ? "this chat is still being written; wait for it to finish"
      : messages.length === 0 ? "nothing to save: no messages found" : null;
    return C.envelope({ site, id: conversationId, title: resp.title, exportedAt, complete: !reason, reason, branch, messages, raw: resp });
  }

  async function getJson(url, headers) {
    const r = await fetch(url, { credentials: "include", headers: Object.assign({ Accept: "application/json" }, headers) });
    if (!r.ok) throw new Error("chatgpt.com answered " + r.status + " for " + new URL(url, location.href).pathname.replace(/[0-9a-f-]{36}/g, ":id"));
    return r.json();
  }
  async function fetchConversation(id) {
    const session = await getJson("/api/auth/session");
    if (!session || typeof session.accessToken !== "string") throw new Error("chatgpt.com gave no access token; are you logged in?");
    return getJson("/backend-api/conversation/" + encodeURIComponent(id), { Authorization: "Bearer " + session.accessToken });
  }

  return { toEnvelope, fetchConversation };
});
