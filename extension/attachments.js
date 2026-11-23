// Scriptorium — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
// Save attachments: the chat's non-text items, fetched one after another, put in one store-only zip. Pure functions (tested)
// and the plain ZIP writer; the export window does the fetching (through the page) and the saving.
(function (root, factory) {
  const common = typeof module === "object" && module.exports ? require("./common.js") : root.Scriptorium.common;
  const api = factory(common);
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.Scriptorium.attachments = api;
})(globalThis, function (C) {
  const EXT = { "image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif", "application/pdf": ".pdf", "text/plain": ".txt", "text/csv": ".csv", "application/json": ".json", "application/zip": ".zip" };

  // `capture-<site>-<encoded id>-attachments.zip`, beside capture-<site>-<encoded id>.json.
  function zipName(site, id) { return "capture-" + site + "-" + C.encodeId(id) + "-attachments.zip"; }

  // The items of an envelope in order: {messageId, n, item, marker}. n is the item's place in its message, as in the marker.
  function listItems(env) {
    const out = [];
    for (const m of (env && Array.isArray(env.messages) ? env.messages : [])) {
      (Array.isArray(m.attachments) ? m.attachments : []).forEach((item, i) => out.push({ messageId: m.id || "message", n: i + 1, item, marker: C.itemMarker(item, i + 1) }));
    }
    return out;
  }

  // A file name safe on every system: letters, digits, dot, dash, underscore, space; no leading dot; at most 80 characters.
  function safeName(name) {
    const s = String(name || "").replace(/[^\p{L}\p{N}._ -]/gu, "_").replace(/^[.\s]+/, "").trim();
    if (s.length <= 80) return s;
    const dot = s.lastIndexOf(".");
    return dot > 0 && s.length - dot <= 10 ? s.slice(0, 80 - (s.length - dot)) + s.slice(dot) : s.slice(0, 80);
  }
  // files/<message id>-<n>-<name>; a name with no dot gets the extension its type suggests.
  function entryName(entry) {
    let name = safeName(entry.item.name) || entry.item.kind;
    if (!/\.[A-Za-z0-9]{1,8}$/.test(name)) name += EXT[entry.item.mime] || "";
    return "files/" + safeName(entry.messageId) + "-" + entry.n + "-" + name;
  }

  function toBase64(bytes) {
    let s = "";
    for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    return btoa(s);
  }
  function fromBase64(text) {
    const s = atob(text), out = new Uint8Array(s.length);
    for (let i = 0; i < s.length; i++) out[i] = s.charCodeAt(i);
    return out;
  }

  const CHUNK = 4 * 1024 * 1024;   // raw bytes per message; base64 makes it about 5.6 MB, far under the extension-message limit

  // One item from the page: ask(msg) is the message round trip. The first reply names the size; the bytes then come in CHUNK pieces.
  async function pull(ask, fetchMsg) {
    const first = await ask(fetchMsg);
    if (!first || !first.ok) throw new Error((first && first.error) || "the page did not answer");
    const out = new Uint8Array(first.size), n = Math.ceil(first.size / (first.chunk || CHUNK));
    for (let i = 0, at = 0; i < n; i++) {
      const r = await ask({ type: "scriptorium-attachment-chunk", index: i });
      if (!r || !r.ok) throw new Error((r && r.error) || "the page did not answer");
      const part = fromBase64(r.base64);
      if (at + part.length > out.length) throw new Error("the item came back larger than announced");
      out.set(part, at); at += part.length;
      if (i === n - 1 && at !== out.length) throw new Error("the item came back incomplete");
    }
    return { bytes: out, mime: first.mime };
  }

  // One request after another, never in parallel. fetchOne(entry) resolves {bytes, mime} or throws; a throw makes that item
  // "missing" with the reason, and the rest go on. Returns [{entry, status: "saved"|"missing", reason, bytes, mime}].
  async function collect(entries, fetchOne, onProgress) {
    const results = [];
    for (let i = 0; i < entries.length; i++) {
      if (onProgress) onProgress(i, entries.length);
      try {
        const got = await fetchOne(entries[i]);
        if (!got || !(got.bytes instanceof Uint8Array)) throw new Error("nothing came back");
        results.push({ entry: entries[i], status: "saved", reason: null, bytes: got.bytes, mime: got.mime || entries[i].item.mime || null });
      } catch (e) {
        results.push({ entry: entries[i], status: "missing", reason: String((e && e.message) || e).slice(0, 200), bytes: null, mime: null });
      }
      if (onProgress) onProgress(i + 1, entries.length);
    }
    return results;
  }
  // Save is blocked only when nothing at all could be fetched.
  function anySaved(results) { return results.some((r) => r.status === "saved"); }

  // One distinct path per saved item: two names that clean to the same text get "-2", "-3" before the extension.
  function paths(results) {
    const used = new Set(), out = new Map();
    for (const r of results) {
      if (r.status !== "saved") continue;
      let p = entryName(r.entry);
      if (used.has(p)) {
        const dot = p.lastIndexOf("."), slash = p.lastIndexOf("/");
        const [stem, ext] = dot > slash ? [p.slice(0, dot), p.slice(dot)] : [p, ""];
        let k = 2;
        while (used.has(stem + "-" + k + ext)) k++;
        p = stem + "-" + k + ext;
      }
      used.add(p); out.set(r, p);
    }
    return out;
  }

  function manifest(results, where = paths(results)) {
    return results.map((r) => ({
      message_id: r.entry.messageId, n: r.entry.n, marker: r.entry.marker, name: r.entry.item.name, mime: r.mime || r.entry.item.mime,
      size: r.bytes ? r.bytes.length : r.entry.item.size, status: r.status, reason: r.reason, file: where.get(r) || null,
    }));
  }

  // ---- the ZIP writer: store only (no compression), UTF-8 names, CRC-32. shortcut: no ZIP64, so one zip is under 4 GB and 65535 files.
  let table = null;
  function crc32(bytes) {
    if (!table) { table = new Uint32Array(256); for (let n = 0; n < 256; n++) { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320 ^ (c >>> 1) : c >>> 1; table[n] = c >>> 0; } }
    let c = 0xFFFFFFFF;
    for (let i = 0; i < bytes.length; i++) c = table[(c ^ bytes[i]) & 0xFF] ^ (c >>> 8);
    return (c ^ 0xFFFFFFFF) >>> 0;
  }
  const u16 = (v) => [v & 255, (v >>> 8) & 255];
  const u32 = (v) => [v & 255, (v >>> 8) & 255, (v >>> 16) & 255, (v >>> 24) & 255];

  // files: [{name, data: Uint8Array}] -> an array of Uint8Array parts (a Blob takes them as they are).
  function zipParts(files, when) {
    const d = when || new Date();
    const time = (d.getHours() << 11) | (d.getMinutes() << 5) | (d.getSeconds() >> 1);
    const date = (Math.max(d.getFullYear() - 1980, 0) << 9) | ((d.getMonth() + 1) << 5) | d.getDate();
    if (files.length > 65535) throw new Error("too many files for one zip");
    const parts = [], central = [];
    let offset = 0;
    for (const f of files) {
      const name = new TextEncoder().encode(f.name), crc = crc32(f.data), size = f.data.length;
      if (size >= 0xFFFFFFFF || offset >= 0xFFFFFFFF) throw new Error("these files are too big for one zip");
      const head = Uint8Array.from([...u32(0x04034b50), ...u16(20), ...u16(0x0800), ...u16(0), ...u16(time), ...u16(date), ...u32(crc), ...u32(size), ...u32(size), ...u16(name.length), ...u16(0)]);
      parts.push(head, name, f.data);
      central.push(Uint8Array.from([...u32(0x02014b50), ...u16(20), ...u16(20), ...u16(0x0800), ...u16(0), ...u16(time), ...u16(date), ...u32(crc), ...u32(size), ...u32(size), ...u16(name.length), ...u16(0), ...u16(0), ...u16(0), ...u16(0), ...u32(0), ...u32(offset)]), name);
      offset += head.length + name.length + size;
    }
    const cdSize = central.reduce((a, p) => a + p.length, 0);
    return parts.concat(central, [Uint8Array.from([...u32(0x06054b50), ...u16(0), ...u16(0), ...u16(files.length), ...u16(files.length), ...u32(cdSize), ...u32(offset), ...u16(0)])]);
  }

  // The finished zip's parts: attachments.json first, then files/<...> for each saved item.
  function archiveParts(results, when) {
    const where = paths(results);
    const files = [{ name: "attachments.json", data: new TextEncoder().encode(JSON.stringify(C.scrub(manifest(results, where)), null, 2)) }];
    for (const r of results) if (r.status === "saved") files.push({ name: where.get(r), data: r.bytes });
    return zipParts(files, when);
  }

  return { zipName, listItems, safeName, entryName, paths, toBase64, fromBase64, CHUNK, pull, collect, anySaved, manifest, crc32, zipParts, archiveParts };
});
