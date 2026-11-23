// Scriptorium — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
// grok.com reader. VERIFY LIVE BEFORE RELEASE: Grok's own conversation endpoint is not confirmed. toEnvelope reads the
// shape of Grok's official export (engine/grok_export.py); fetchConversation reuses the request the page itself already
// made for this conversation, and says so plainly when it finds none.
(function (root, factory) {
  const common = typeof module === "object" && module.exports ? require("../common.js") : root.Scriptorium.common;
  const api = factory(common);
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.Scriptorium.sites.grok = api;
})(globalThis, function (C) {
  const isObj = (v) => v !== null && typeof v === "object" && !Array.isArray(v);

  function epoch(v) {   // {"$date": {"$numberLong": ms}} | {"$date": ms|iso} | ISO text | number (ms or s) -> seconds, or null
    if (isObj(v) && "$date" in v) { v = v.$date; if (isObj(v)) v = v.$numberLong; if (typeof v === "string" && /^-?\d+$/.test(v)) v = Number(v); if (typeof v === "number") return v / 1000; }
    if (typeof v === "number") return v > 1e11 ? v / 1000 : v;
    if (typeof v === "string" && v.trim()) {
      if (/^-?\d+$/.test(v.trim())) { const n = Number(v); return Math.abs(n) > 1e11 ? n / 1000 : n; }
      const t = Date.parse(v); return Number.isNaN(t) ? null : t / 1000;
    }
    return null;
  }

  // siteResponse: {conversation: {id, title, leaf_response_id}, responses: [{response: {_id, message, sender, parent_response_id, create_time}}]}
  function toEnvelope(resp, conversationId, exportedAt) {
    const site = "grok";
    if (isObj(resp) && typeof resp.unavailable === "string") return C.envelope({ site, id: conversationId, exportedAt, complete: false, reason: resp.unavailable, raw: null });
    if (!isObj(resp) || !Array.isArray(resp.responses)) return C.changed(site, conversationId, exportedAt, resp, "no responses");
    const conv = isObj(resp.conversation) ? resp.conversation : {};
    const byId = new Map(), order = [];
    for (const r of resp.responses) {
      const x = isObj(r) && isObj(r.response) ? r.response : r;
      if (!isObj(x) || typeof x._id !== "string" || !x._id) return C.changed(site, conversationId, exportedAt, resp, "unexpected response");
      byId.set(x._id, x); order.push(x);
    }
    let leaf = conv.leaf_response_id;
    if ((typeof leaf !== "string" || !byId.has(leaf)) && order.length) {   // no usable leaf: the latest-created message's chain (a tie goes to the later one)
      let best = 0, bestT = -Infinity;
      order.forEach((x, i) => { const t = epoch(x.create_time) || 0; if (t >= bestT) { best = i; bestT = t; } });
      leaf = order[best]._id;
    }
    if (typeof leaf !== "string") return C.envelope({ site, id: conversationId, title: conv.title, exportedAt, complete: false, reason: "nothing to save: no messages found", raw: resp });
    const { path, top } = C.walkBranch(byId, leaf, (id) => byId.get(id).parent_response_id);
    const messages = [], branch = [];
    let streaming = false;
    for (const id of path) {
      const x = byId.get(id);
      if (x.partial === true || x.streaming === true) streaming = true;   // shortcut: field names unverified live
      if (typeof x.message === "string" && x.message.trim()) {
        messages.push({ id, role: x.sender === "human" ? "user" : "assistant", text: x.message, created_at: C.isoFromEpoch(epoch(x.create_time)) });
        branch.push(id);
      }
    }
    const reason = !top ? "the earliest turns of this chat are missing" : streaming ? "this chat is still being written; wait for it to finish"
      : messages.length === 0 ? "nothing to save: no messages found" : null;
    return C.envelope({ site, id: conversationId, title: conv.title, exportedAt, complete: !reason, reason, branch, messages, raw: resp });
  }

  // The page's own requests for this conversation (same origin, id in the path), newest first, each once. No list endpoint is used.
  function candidateUrls(names, origin, id) {
    const enc = encodeURIComponent(id), out = [];
    for (const n of names.slice().reverse()) {
      let u; try { u = new URL(n); } catch (e) { continue; }
      if (u.origin !== origin || !u.pathname.startsWith("/rest/")) continue;
      if (!(u.pathname.includes("/" + enc + "/") || u.pathname.endsWith("/" + enc))) continue;
      const path = u.pathname + u.search;
      if (!out.includes(path)) out.push(path);
    }
    return out;
  }

  // GET each candidate until one answers OK with the expected shape. None does: say so, and toEnvelope marks it incomplete.
  async function fetchConversation(id, env) {
    const e = env || {};
    const names = e.names || (globalThis.performance ? performance.getEntriesByType("resource").map((x) => x.name) : []);
    const doFetch = e.fetch || ((...a) => globalThis.fetch(...a));   // a bare fetch call loses its window and throws Illegal invocation
    const urls = candidateUrls(names, e.origin || location.origin, id);
    if (!urls.length) return { unavailable: "Grok has not loaded this conversation's data yet (unverified reader); reload the page and try again" };
    for (const path of urls) {
      try {
        const r = await doFetch(path, { method: "GET", credentials: "include", headers: { Accept: "application/json" } });
        if (!r.ok) continue;
        const data = await r.json();
        if (isObj(data) && Array.isArray(data.responses)) return data;
      } catch (err) { /* try the next candidate */ }
    }
    return { unavailable: "none of the requests Grok made for this chat gave its messages (unverified reader)" };
  }

  return { toEnvelope, fetchConversation, candidateUrls };
});
