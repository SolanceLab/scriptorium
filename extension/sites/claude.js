// Scriptorium — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
// claude.ai reader. toEnvelope is pure (tested); fetchConversation asks this one conversation from the logged-in page.
(function (root, factory) {
  const common = typeof module === "object" && module.exports ? require("../common.js") : root.Scriptorium.common;
  const api = factory(common);
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.Scriptorium.sites.claude = api;
})(globalThis, function (C) {
  const ZERO = "00000000-0000-4000-8000-000000000000";   // parent_message_uuid of a first message

  function text(m) {
    const blocks = Array.isArray(m.content) ? m.content.filter((b) => b && b.type === "text" && typeof b.text === "string").map((b) => b.text).join("\n") : "";
    return blocks.trim() ? blocks : typeof m.text === "string" ? m.text : "";
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
      const t = text(m);
      if (!role || !t.trim()) continue;
      messages.push({ id, role, text: t, created_at: typeof m.created_at === "string" ? m.created_at : null });
      branch.push(id);
    }
    const reason = !top ? "the earliest turns of this chat are missing" : streaming ? "this chat is still being written; wait for it to finish"
      : messages.length === 0 ? "nothing to save: no messages found" : null;
    return C.envelope({ site, id: conversationId, title: resp.name, exportedAt, complete: !reason, reason, branch, messages, raw: resp });
  }

  async function getJson(url) {
    const r = await fetch(url, { credentials: "include", headers: { Accept: "application/json" } });
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

  return { toEnvelope, fetchConversation };
});
