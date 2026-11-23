// Scriptorium — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
// The toolbar button opens one small export window for the active tab (or focuses the one already open for it).
// Nothing else happens here: no blobs, no downloads, no storage. The window does the export and the save.
importScripts("common.js");
const C = globalThis.Scriptorium.common;

const opening = new Map();   // tabId -> in-flight open, so two quick clicks make one window (the worker lives seconds, not hours)

async function openFor(tab) {
  const url = chrome.runtime.getURL(C.exportPagePath(tab.id));
  // Found by its address, not remembered in worker memory, so a restarted worker still finds it.
  const windows = await chrome.windows.getAll({ populate: true, windowTypes: ["popup"] });
  const mine = windows.find((w) => (w.tabs || []).some((t) => t.url === url));
  if (mine) {
    await chrome.windows.update(mine.id, { focused: true });
    // The tab may have moved to another page since the window started: tell it to look again.
    try { await chrome.runtime.sendMessage({ type: "scriptorium-recheck", tabId: tab.id }); } catch (e) { /* window closing; nothing to recheck */ }
    return;
  }
  await chrome.windows.create({ type: "popup", url, width: 420, height: 360 });
}

chrome.action.onClicked.addListener((tab) => {
  if (!tab || typeof tab.id !== "number") return;
  if (opening.has(tab.id)) return opening.get(tab.id);
  const p = openFor(tab).finally(() => opening.delete(tab.id));
  opening.set(tab.id, p);
  return p;
});
