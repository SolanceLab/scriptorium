// Scriptorium — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
// Runs on the three chat sites and does nothing until the export window asks. One ask = the one conversation in this tab.
(function () {
  const S = globalThis.Scriptorium;
  chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    if (!msg || msg.type !== "scriptorium-export") return false;
    (async () => {
      const page = S.common.parseConversationUrl(location.href);
      if (!page) return sendResponse({ ok: false, error: "Open one conversation first." });
      const reader = S.sites[page.site];
      const data = await reader.fetchConversation(page.id);
      sendResponse({ ok: true, envelope: reader.toEnvelope(data, page.id, S.common.nowIso()) });
    })().catch((e) => sendResponse({ ok: false, error: String((e && e.message) || e) }));
    return true;
  });
})();
