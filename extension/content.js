// Scriptorium — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
// Runs on the three chat sites and does nothing until the export window asks. One ask = the one conversation in this tab.
(function () {
  const S = globalThis.Scriptorium;
  let held = null;
  chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    if (msg && msg.type === "scriptorium-attachment-chunk") {   // bytes [index*CHUNK, +CHUNK) of the held item, base64
      const at = msg.index * S.attachments.CHUNK;
      if (!held || !(at >= 0) || at > held.length) { sendResponse({ ok: false, error: "No fetched item is waiting." }); return false; }
      sendResponse({ ok: true, base64: S.attachments.toBase64(held.subarray(at, at + S.attachments.CHUNK)) });
      if (at + S.attachments.CHUNK >= held.length) held = null;
      return false;
    }
    if (msg && msg.type === "scriptorium-fetch-attachment") {   // one item of this chat, as its original bytes (base64: messages carry text, not binary)
      (async () => {
        const page = S.common.parseConversationUrl(location.href);
        if (!page || page.site !== msg.site || page.id !== msg.conversation_id) return sendResponse({ ok: false, error: "The open chat changed." });
        const got = await S.sites[page.site].fetchAttachment(msg.item, page.id);
        held = got.bytes;   // one item at a time; the window pulls it in bounded chunks and so stays under the message size limit
        sendResponse({ ok: true, size: held.length, chunk: S.attachments.CHUNK, mime: got.mime || null });
      })().catch((e) => sendResponse({ ok: false, error: String((e && e.message) || e) }));
      return true;
    }
    if (!msg || msg.type !== "scriptorium-export") return false;
    (async () => {
      const open = () => { const p = S.common.parseConversationUrl(location.href); return p && p.site === msg.site && p.id === msg.id ? p : null; };
      const page = S.common.parseConversationUrl(location.href);
      if (!page) return sendResponse({ ok: false, error: "Open one conversation first." });
      if (!open()) return sendResponse({ ok: false, error: "a different chat is open now" });   // the window asked for another chat than this tab shows
      const reader = S.sites[page.site];
      const data = await reader.fetchConversation(page.id);
      if (!open()) return sendResponse({ ok: false, error: "a different chat is open now" });   // the tab moved on while fetching
      sendResponse({ ok: true, envelope: reader.toEnvelope(data, page.id, S.common.nowIso()) });
    })().catch((e) => sendResponse({ ok: false, error: String((e && e.message) || e) }));
    return true;
  });
})();
