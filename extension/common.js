// Scriptorium — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
// Shared by the export window, the content script and the tests: the capture file's name and shape (the phase 1 envelope of
// engine/capture_file.py), the conversation-page check, and the branch walk the three site readers use.
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else { root.Scriptorium = root.Scriptorium || { sites: {} }; root.Scriptorium.common = api; }
})(globalThis, function () {
  const SCHEMA_VERSION = 1;
  const SITES = ["chatgpt", "claude", "grok"];
  const CHANGED = "this site changed; update the extension";

  // Python's quote(id, safe=""): everything but letters, digits and _.-~ becomes %XX (UTF-8, upper-case hex).
  function encodeId(id) {
    return encodeURIComponent(String(id)).replace(/[!'()*]/g, (c) => "%" + c.charCodeAt(0).toString(16).toUpperCase());
  }
  function captureName(site, id) { return "capture-" + site + "-" + encodeId(id) + ".json"; }

  // One conversation page only: the site and the conversation id, or null (a list, a project, a settings page...).
  const PAGES = [
    ["claude", "claude.ai", /^\/chat\/([^/]+)$/],
    ["chatgpt", "chatgpt.com", /^(?:\/g\/[^/]+)?\/c\/([^/]+)$/],   // plain chats, and chats inside a project or a custom GPT
    ["grok", "grok.com", /^\/(?:chat|c)\/([^/]+)$/],
  ];
  function parseConversationUrl(url) {
    let u;
    try { u = new URL(url); } catch (e) { return null; }
    if (u.protocol !== "https:") return null;
    for (const [site, host, re] of PAGES) {
      const m = u.hostname === host && re.exec(u.pathname.replace(/\/+$/, "") || "/");
      if (m) { try { return { site, id: decodeURIComponent(m[1]) }; } catch (e) { return null; } }
    }
    return null;
  }

  function isoFromEpoch(sec) {
    if (typeof sec !== "number" || !Number.isFinite(sec)) return null;
    const d = new Date(sec * 1000);
    return Number.isNaN(d.getTime()) ? null : d.toISOString().replace(/\.\d+Z$/, "Z");
  }
  function nowIso() { return new Date().toISOString().replace(/\.\d+Z$/, "Z"); }

  // The phase 1 envelope. `reason` (extra to the schema) says why a file is incomplete; the importer ignores it.
  function envelope({ site, id, title, exportedAt, complete, reason, branch, messages, raw }) {
    const env = {
      schema_version: SCHEMA_VERSION, site, conversation_id: id,
      title: typeof title === "string" && title.trim() ? title : "Untitled conversation",
      exported_at: exportedAt, complete: complete === true,
      branch: branch || [], messages: messages || [], raw: raw === undefined ? null : raw,
    };
    if (!env.complete) env.reason = reason || CHANGED;
    return env;
  }
  // A response we do not recognise: never a partial file marked complete.
  function changed(site, id, exportedAt, raw, why) {
    return envelope({ site, id, exportedAt, complete: false, reason: CHANGED + (why ? " (" + why + ")" : ""), raw });
  }

  // From `leaf` up the parents. `byId` is a Map, `parentOf(id)` the parent id. Reaching the top (parent null/empty,
  // or `isTop(parent)`) is complete; a parent that is missing or a loop means earlier turns are not there.
  function walkBranch(byId, leaf, parentOf, isTop) {
    const path = [], seen = new Set();
    let cur = leaf, top = false;
    for (;;) {
      if (cur === null || cur === undefined || cur === "" || (path.length && isTop && isTop(cur))) { top = path.length > 0; break; }
      if (typeof cur !== "string" || !byId.has(cur) || seen.has(cur)) break;
      seen.add(cur); path.push(cur); cur = parentOf(cur);
    }
    return { path: path.reverse(), top };
  }

  // Non-text items. One item = {id, kind: image|file, origin: uploaded|generated, name, mime, size, width, height, pointer}
  // (every field text/number or null). The readers build a message from "pieces": a string (words) or an item (stands at its place).
  const num = (v) => (typeof v === "number" && Number.isFinite(v) ? v : null);
  const txt = (v) => (typeof v === "string" && v.trim() ? v : null);
  function makeItem(o) {
    return {
      id: txt(o.id), kind: o.kind === "image" ? "image" : "file", origin: o.origin === "generated" ? "generated" : "uploaded",
      name: txt(o.name), mime: txt(o.mime), size: num(o.size), width: num(o.width), height: num(o.height), pointer: txt(o.pointer),
    };
  }
  // The short marker left in the text at the item's place; n is its 1-based place among the message's items.
  function itemMarker(item, n) {
    return item.kind === "image" ? "[image: " + item.origin + " · " + (item.name || n) + "]" : "[file: " + (item.name || n) + " · " + (item.mime || "unknown") + "]";
  }
  // pieces -> {text, items}: words stay, each item becomes its marker on its own line, blank words drop.
  function renderPieces(pieces) {
    const lines = [], items = [];
    for (const p of pieces) {
      if (typeof p === "string") { if (p.trim()) lines.push(p); }
      else if (p) { items.push(p); lines.push(itemMarker(p, items.length)); }
    }
    return { text: lines.join("\n"), items };
  }
  // A saved message: `attachments` is an optional field, present only when the message has items.
  function message(id, role, text, createdAt, items) {
    const m = { id, role, text, created_at: createdAt };
    if (items && items.length) m.attachments = items;
    return m;
  }

  // GET one file's bytes: {bytes: Uint8Array, mime}. The error names the path with ids hidden, never the signed query.
  async function getBytes(doFetch, url, init) {
    const r = await doFetch(url, Object.assign({ method: "GET" }, init));
    if (!r.ok) throw new Error(new URL(url, "https://x.invalid").pathname.replace(/[0-9a-f-]{36}/g, ":id").slice(0, 60) + " answered " + r.status);
    const ct = r.headers && typeof r.headers.get === "function" ? r.headers.get("content-type") : null;
    return { bytes: new Uint8Array(await r.arrayBuffer()), mime: ct ? ct.split(";")[0].trim() : null };
  }
  // The url as an https address on `origin` (a path or a full address), or null when it points anywhere else.
  function sameOriginUrl(url, origin) {
    try { const u = new URL(url, origin); return u.protocol === "https:" && u.origin === origin ? u : null; } catch (e) { return null; }
  }

  function preview(env) {
    return { site: env.site, title: env.title, count: env.messages.length, complete: env.complete === true, reason: env.reason || null };
  }

  // Save is allowed only for a copy that says it is complete.
  function canSave(env) { return !!env && env.complete === true; }
  // The bytes of a saved file, as a Blob (never a data: URL, which Chrome caps near 2 MB).
  function captureBlob(env) { return new Blob([JSON.stringify(env, null, 2)], { type: "application/json" }); }
  // The exact file name the importer reads, only for a complete envelope.
  function captureFilename(env) { return canSave(env) ? captureName(env.site, env.conversation_id) : null; }

  // The export window is opened for one tab: export.html?tab=<id>. tabIdFromSearch is the strict reverse (null if not a plain id).
  function exportPagePath(tabId) { return "export.html?tab=" + encodeURIComponent(String(tabId)); }
  function tabIdFromSearch(search) {
    const m = /^\?tab=(\d{1,12})$/.exec(String(search || ""));
    return m ? Number(m[1]) : null;
  }

  // What a chrome.downloads.onChanged delta means: null (still going), { ok: true } or { error } ("Cancelled" for a cancel).
  function downloadOutcome(delta) {
    if (!delta || !delta.state) return null;
    if (delta.state.current === "complete") return { ok: true };
    if (delta.state.current === "interrupted") {
      const e = delta.error && delta.error.current;
      return { error: e === "USER_CANCELED" ? "Cancelled" : e || "interrupted" };
    }
    return null;
  }

  // Runs fn at most once (revoking a blob URL on complete, on interrupt and on unload: only the first counts).
  function once(fn) { let done = false; return (...a) => { if (done) return false; done = true; fn(...a); return true; }; }

  return { getBytes, sameOriginUrl, makeItem, itemMarker, renderPieces, message, canSave, captureBlob, captureFilename, exportPagePath, tabIdFromSearch, downloadOutcome, once, SCHEMA_VERSION, SITES, CHANGED, encodeId, captureName, parseConversationUrl, isoFromEpoch, nowIso, envelope, changed, walkBranch, preview };
});
