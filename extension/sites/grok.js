// Scriptorium — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
// grok.com reader. Grok keeps a chat as a tree of response nodes: fetchConversation asks Grok's own endpoints from the logged-in
// page (the node tree, then the texts of the chosen branch only); toEnvelope turns that into the capture file. Shapes verified
// live 23 Nov 2026 (names of fields only). engine/grok_export.py reads Grok's official export, a different shape.
(function (root, factory) {
  const common = typeof module === "object" && module.exports ? require("../common.js") : root.Scriptorium.common;
  const api = factory(common);
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.Scriptorium.sites.grok = api;
})(globalThis, function (C) {
  const isObj = (v) => v !== null && typeof v === "object" && !Array.isArray(v);
  const BATCH = 100;
  const STILL_WRITING = "Grok is still writing a reply";

  function epoch(v) {   // ISO text | {"$date": ...} | {seconds} | number (ms or s) -> seconds, or null
    if (isObj(v) && "$date" in v) { v = v.$date; if (isObj(v)) v = v.$numberLong; if (typeof v === "string" && /^-?\d+$/.test(v)) v = Number(v); if (typeof v === "number") return v / 1000; }
    if (isObj(v) && "seconds" in v) { const n = Number(v.seconds); return Number.isFinite(n) ? n : null; }
    if (typeof v === "number") return v > 1e11 ? v / 1000 : v;
    if (typeof v === "string" && v.trim()) {
      if (/^-?\d+$/.test(v.trim())) { const n = Number(v); return Math.abs(n) > 1e11 ? n / 1000 : n; }
      const t = Date.parse(v); return Number.isNaN(t) ? null : t / 1000;
    }
    return null;
  }

  const list = (v) => (Array.isArray(v) ? v : []);
  const looksLikeAddress = (s) => typeof s === "string" && /^(https?:\/\/|\/)/.test(s);
  // shortcut: a relative file address is taken to live on Grok's assets host; confirm on a chat with a real file.
  const ASSETS = "https://assets.grok.com/";
  // A bare path may only resolve to https://assets.grok.com; anything that would land elsewhere is no address.
  function absolute(s) {
    if (typeof s !== "string" || !s) return null;
    if (/^https?:\/\//.test(s) || s.startsWith("/")) return s;
    try { return new URL(ASSETS + s).origin === "https://assets.grok.com" ? ASSETS + s : null; } catch (e) { return null; }
  }

  // The items a response shows (phase 4 shape): generated images; uploaded images; uploaded files (+ their names and types).
  function assetItems(x, uploaded, origin = "uploaded") {   // origin: who added these attachments to the turn
    const out = [];
    for (const u of list(x.generatedImageUrls)) {
      const addr = isObj(u) ? u.url || u.imageUrl : u;
      if (typeof addr === "string" && addr) out.push(C.makeItem({ id: addr, kind: "image", origin: "generated", pointer: absolute(addr) }));
    }
    if (!uploaded) return out;
    for (const a of list(x.imageAttachments)) {
      const o = isObj(a) ? a : looksLikeAddress(a) ? { url: a } : { id: a };
      const addr = o.url || o.uri || o.fileUri || (typeof a === "string" && looksLikeAddress(a) ? a : null);
      out.push(C.makeItem({ id: o.id || o.fileId || addr, kind: "image", origin, name: o.name || o.fileName, mime: o.mimeType || o.mime_type, size: o.size, pointer: absolute(addr) }));
    }
    const meta = list(x.fileAttachmentsMetadata), uris = list(x.fileUris), files = list(x.fileAttachments);
    const n = Math.max(files.length, uris.length);
    for (let i = 0; i < n; i++) {
      const a = files[i], id = isObj(a) ? a.id || a.fileId : a;
      const idOf = (k) => k.fileMetadataId || k.id || k.fileId || null;
      const byId = meta.find((k) => isObj(k) && id && idOf(k) === id);
      const pos = isObj(meta[i]) && (!id || !idOf(meta[i])) ? meta[i] : null;   // by position only when neither side names another file
      const m = byId || pos || {};
      const mime = m.fileMimeType || m.mimeType || m.mime_type || m.mime || (isObj(a) ? a.mimeType || a.mime_type : null);
      // The file's full path (users/<…>/<…>/content on Grok's assets host) is in its metadata; fileUris may hold only an id.
      const uri = (typeof m.fileUri === "string" && m.fileUri) ? m.fileUri
        : typeof uris[i] === "string" ? uris[i] : isObj(uris[i]) ? uris[i].url || uris[i].uri : null;
      out.push(C.makeItem({ id: id || uri, kind: /^image\//.test(mime || "") ? "image" : "file", origin, name: m.fileName || m.name || (isObj(a) ? a.name : null), mime, size: m.size ?? m.fileSize, pointer: absolute(uri) }));
    }
    return out;
  }

  // A response shows something only when it has a text (an empty text is a real empty turn) or an attachment.
  const hasContent = (x) => typeof x.message === "string" || assetItems(x, true).length > 0;

  // The leaf of the branch the person saw: the page's rid when it names a node; else the newest childless node (by
  // createTime among `times`, a Map id -> seconds; a tie or no times goes to the later node in the list).
  function pickLeaf(nodes, rid, times) {
    const ids = new Set(nodes.map((n) => n.responseId));
    if (typeof rid === "string" && ids.has(rid)) return rid;
    const parents = new Set(nodes.map((n) => n.parentResponseId).filter(Boolean));
    const ends = nodes.filter((n) => !parents.has(n.responseId));
    let best = null, bestT = -Infinity;
    for (const n of ends) { const t = (times && times.get(n.responseId)) || 0; if (t >= bestT) { best = n.responseId; bestT = t; } }
    return best;
  }

  // siteResponse: {title, rid?, nodes: [{responseId, sender, parentResponseId}], responses: [{responseId, message, sender, createTime, partial, ...}], inflight: []}
  function toEnvelope(resp, conversationId, exportedAt) {
    const site = "grok";
    if (isObj(resp) && typeof resp.unavailable === "string") return C.envelope({ site, id: conversationId, exportedAt, complete: false, reason: resp.unavailable, raw: null });
    if (!isObj(resp) || !Array.isArray(resp.nodes) || !Array.isArray(resp.responses)) return C.changed(site, conversationId, exportedAt, resp, "no nodes or responses");
    const nodes = resp.nodes;
    if (!nodes.every((n) => isObj(n) && typeof n.responseId === "string" && n.responseId)) return C.changed(site, conversationId, exportedAt, resp, "unexpected node");
    const byId = new Map();
    for (const x of resp.responses) {
      if (!isObj(x) || typeof x.responseId !== "string" || !x.responseId) return C.changed(site, conversationId, exportedAt, resp, "unexpected response");
      byId.set(x.responseId, x);
    }
    // response-node returns the whole tree. An empty parent is the first message. A parent that is not one of its nodes (an id
    // Grok keeps outside the tree) is the start only when exactly one node has such a parent; with more, history is missing.
    const nodeIds = new Set(nodes.map((n) => n.responseId));
    const outside = nodes.filter((n) => n.parentResponseId && !nodeIds.has(n.parentResponseId));
    // A node with no parent is a root too: with one already there, an outside parent is a second start, not the first.
    const hasRoot = nodes.some((n) => !n.parentResponseId);
    const parentOf = new Map(nodes.map((n) => [n.responseId, nodeIds.has(n.parentResponseId) ? n.parentResponseId : (outside.length === 1 && !hasRoot) || !n.parentResponseId ? null : "\0outside"]));
    const times = new Map(resp.responses.map((x) => [x.responseId, epoch(x.createTime)]));
    const leaf = typeof resp.leaf === "string" && parentOf.has(resp.leaf) ? resp.leaf : pickLeaf(nodes, resp.rid, times);
    const inflight = Array.isArray(resp.inflight) && resp.inflight.length > 0;
    if (typeof leaf !== "string") return C.envelope({ site, id: conversationId, title: resp.title, exportedAt, complete: false, reason: inflight ? STILL_WRITING : "nothing to save: no messages found", raw: resp });
    const { path, top } = C.walkBranch(byId, leaf, (id) => parentOf.has(id) ? parentOf.get(id) : byId.get(id).parentResponseId);
    if (!path.every((id) => hasContent(byId.get(id)))) return C.changed(site, conversationId, exportedAt, resp, "a response has no message");
    const messages = [], branch = [];
    let streaming = inflight;
    for (const id of path) {
      const x = byId.get(id);
      if (x.partial === true || x.streaming === true) streaming = true;
      const sender = String(x.sender || "").toLowerCase();
      const role = sender === "human" || sender === "user" ? "user" : "assistant";
      const r = C.renderPieces([typeof x.message === "string" ? x.message : "", ...assetItems(x, true, role === "user" ? "uploaded" : "generated")]);
      if (r.text.trim()) {
        messages.push(C.message(id, role, r.text, C.isoFromEpoch(epoch(x.createTime)), r.items));
        branch.push(id);
      }
    }
    const reason = !top ? "the earliest turns of this chat are missing" : streaming ? STILL_WRITING
      : messages.length === 0 ? "nothing to save: no messages found" : null;
    return C.envelope({ site, id: conversationId, title: resp.title, exportedAt, complete: !reason, reason, branch, messages, raw: resp });
  }

  // The title Grok's page shows, without its trailing " - Grok".
  function pageTitle(e) {
    const t = e.title !== undefined ? e.title : globalThis.document ? document.title : "";
    return typeof t === "string" ? t.replace(/\s*[-–|]\s*Grok\s*$/i, "").trim() : "";
  }

  // The tree, then the texts of the chosen branch (batches of at most 100). Any failure is returned as {unavailable: why}.
  async function fetchConversation(id, env) {
    const e = env || {};
    const doFetch = e.fetch || ((...a) => globalThis.fetch(...a));   // a bare fetch call loses its window and throws Illegal invocation
    const base = "/rest/app-chat/conversations/" + encodeURIComponent(id);
    const fail = (what, r) => ({ unavailable: "Grok did not give " + what + (r && r.status ? " (answered " + r.status + ")" : "") + "; reload the page and try again" });
    let tree;
    try {
      const r = await doFetch(base + "/response-node?includeThreads=true", { method: "GET", credentials: "include", redirect: "error", headers: { Accept: "application/json" } });
      if (!r.ok) return fail("this chat's messages", r);
      tree = await r.json();
    } catch (err) { return fail("this chat's messages"); }
    if (!isObj(tree) || !Array.isArray(tree.responseNodes)) return fail("this chat's messages in the expected form");
    const nodes = tree.responseNodes.filter(isObj);
    const have = new Map();
    const load = async (ids) => {
      for (let i = 0; i < ids.length; i += BATCH) {
        const r = await doFetch(base + "/load-responses", { method: "POST", credentials: "include", redirect: "error", headers: { Accept: "application/json", "Content-Type": "application/json" }, body: JSON.stringify({ responseIds: ids.slice(i, i + BATCH) }) });
        if (!r.ok) throw Object.assign(new Error("load"), { status: r.status });
        const data = await r.json();
        if (!isObj(data) || !Array.isArray(data.responses)) throw new Error("shape");
        for (const x of data.responses) if (isObj(x) && typeof x.responseId === "string" && hasContent(x)) have.set(x.responseId, x);
      }
    };
    try {
      const search = e.search !== undefined ? e.search : globalThis.location ? location.search : "";
      const rid = new URLSearchParams(search).get("rid");
      const known = new Set(nodes.map((n) => n.responseId));
      let leaf = known.has(rid) ? rid : null;
      if (!leaf) {   // no rid: the newest end of the tree; its time is only in the response, so load the ends to compare
        const parents = new Set(nodes.map((n) => n.parentResponseId).filter(Boolean));
        const ends = nodes.filter((n) => !parents.has(n.responseId)).map((n) => n.responseId);
        if (ends.length > 1) {
          await load(ends);
          if (!ends.every((x) => have.has(x))) throw new Error("missing");   // an end that did not come back could be the newest branch
        }
        leaf = pickLeaf(nodes, null, new Map([...have].map(([k, x]) => [k, epoch(x.createTime)])));
      }
      const byParent = new Map(nodes.map((n) => [n.responseId, n.parentResponseId]));
      const { path } = typeof leaf === "string" ? C.walkBranch(byParent, leaf, (x) => byParent.get(x)) : { path: [] };
      await load(path.filter((x) => !have.has(x)));
      return { title: pageTitle(e), rid, leaf, nodes, responses: path.filter((x) => have.has(x)).map((x) => have.get(x)), inflight: Array.isArray(tree.inflightResponses) ? tree.inflightResponses : [] };
    } catch (err) { return fail("the texts of this chat", err); }
  }

  // One item's original, from the address its response named: https only; Grok's own hosts get the login, any other host gets none.
  // shortcut: the file address on Grok's assets host is unverified for uploads; a host that refuses a cross-origin read is listed missing.
  async function fetchAttachment(item, conversationId, env) {
    const e = env || {};
    const doFetch = e.fetch || ((...a) => globalThis.fetch(...a));
    const origin = e.origin || location.origin;
    if (!item || !item.pointer) throw new Error("this item has no download address");
    const u = C.sameOriginUrl(item.pointer, origin) || (() => { try { const x = new URL(item.pointer, origin); return x.protocol === "https:" ? x : null; } catch (err) { return null; } })();
    if (!u) throw new Error("this item's address is not an https address");
    const own = u.hostname === "grok.com" || u.hostname.endsWith(".grok.com");
    const got = await C.getBytes(doFetch, u.href, { credentials: own ? "include" : "omit" });
    return { bytes: got.bytes, mime: got.mime || item.mime };
  }

  return { toEnvelope, fetchConversation, fetchAttachment, pickLeaf };
});
