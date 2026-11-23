// Scriptorium — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
// The export window (export.html?tab=<id>): ask that tab for its one open conversation, show a preview, save on click.
// The blob: URL lives in THIS window only, from Save until the download ends (or the window closes); nothing outlives it.
(function () {
  const C = globalThis.Scriptorium.common;
  const $ = (id) => document.getElementById(id);
  const tabId = C.tabIdFromSearch(location.search);
  let current = null;       // the envelope being previewed
  let revokePending = null; // revokes the live blob URL once, or null when none is live
  let listener = null;      // the onChanged listener of the download in flight
  let generation = 0;       // bumps on every new export and every recheck; a reply from an older one is dropped

  function say(id, text, bad) { const el = $(id); el.textContent = text; el.classList.toggle("bad", !!bad); }

  function endDownload() {   // stop listening and revoke the URL (once)
    if (listener) { chrome.downloads.onChanged.removeListener(listener); listener = null; }
    if (revokePending) { revokePending(); revokePending = null; }
  }
  window.addEventListener("beforeunload", endDownload);
  window.addEventListener("pagehide", endDownload);

  async function exportNow() {
    const mine = ++generation;
    const stale = () => mine !== generation;
    current = null; $("preview").hidden = true; $("save").disabled = true;   // a new export never leaves the last one saveable
    endDownload();
    $("export").disabled = true; say("result", ""); say("status", "Copying this conversation...");
    try {
      const tab = await chrome.tabs.get(tabId);
      if (stale()) return;
      const page = tab && C.parseConversationUrl(tab.url);
      if (!page) return say("status", "Open one conversation first.");
      const reply = await chrome.tabs.sendMessage(tab.id, { type: "scriptorium-export" });
      if (stale()) return;   // a recheck or newer export happened while this one was pending
      if (!reply || !reply.ok) return say("status", (reply && reply.error) || "The page did not answer; reload it and try again.", true);
      const env = reply.envelope;
      if (!env || env.site !== page.site || env.conversation_id !== page.id) return say("status", C.CHANGED, true);   // a different chat opened meanwhile
      current = env;
      const p = C.preview(env);
      $("p-site").textContent = p.site;
      $("p-title").textContent = p.title;
      $("p-count").textContent = String(p.count);
      $("p-state").textContent = p.complete ? "Complete" : "Incomplete: " + p.reason;
      $("preview").hidden = false;
      say("status", p.complete ? "Ready to save." : "This copy cannot be saved.", !p.complete);
      $("save").hidden = false; $("save").disabled = !C.canSave(current);
    } catch (e) {
      if (!stale()) say("status", "Could not reach this page: reload it and try again.", true);
    } finally { if (!stale()) $("export").disabled = false; }
  }

  function finish(outcome) {
    endDownload();
    if (outcome.ok) say("result", "Saved");
    else say("result", outcome.error === "Cancelled" ? "Cancelled" : "Not saved: " + outcome.error, outcome.error !== "Cancelled");
    $("save").disabled = !C.canSave(current); $("export").disabled = false;
  }

  async function save() {
    const filename = C.captureFilename(current);
    if (!filename || revokePending) return;
    $("save").disabled = true; $("export").disabled = true; say("result", "Choose where to save...");   // no new export while a save is open
    const url = URL.createObjectURL(C.captureBlob(current));
    revokePending = C.once(() => URL.revokeObjectURL(url));
    try {
      const id = await chrome.downloads.download({ url, filename, saveAs: true, conflictAction: "overwrite" });
      if (!revokePending) return;   // the window was reset while the dialog opened
      listener = (delta) => {
        if (delta.id !== id) return;
        const out = C.downloadOutcome(delta);
        if (out) finish(out);
      };
      chrome.downloads.onChanged.addListener(listener);
      // It may have ended before the listener was added: look once.
      const [now] = await chrome.downloads.search({ id });
      if (now && listener) {
        const out = C.downloadOutcome({ state: { current: now.state }, error: { current: now.error } });
        if (out) finish(out);
      }
    } catch (e) {
      finish({ error: /cancel/i.test(String(e && e.message)) ? "Cancelled" : String((e && e.message) || e) });
    }
  }

  // Looks at the source tab now: Export shows only on one open conversation. Runs at start and whenever the toolbar button reuses this window.
  async function checkSource() {
    const mine = ++generation;   // invalidates any export still in flight
    current = null; $("preview").hidden = true; $("save").hidden = true; $("save").disabled = true;
    if (!revokePending) say("result", "");
    let ok = false;
    try {
      const tab = tabId === null ? null : await chrome.tabs.get(tabId);
      ok = !!(tab && C.parseConversationUrl(tab.url));
    } catch (e) { ok = false; }
    if (mine !== generation) return;   // a newer check or export took over
    $("export").hidden = !ok;
    $("export").disabled = !ok;   // a refocus during an export must not leave Export greyed out under "Ready"
    say("status", ok ? "Ready. Nothing is copied until you click." : "Open one conversation first.");
  }

  async function start() {
    $("export").addEventListener("click", exportNow);
    $("save").addEventListener("click", save);
    chrome.runtime.onMessage.addListener((m) => {
      if (m && m.type === "scriptorium-recheck" && m.tabId === tabId && !revokePending) checkSource();
    });
    await checkSource();
  }
  start().catch(() => say("status", "Open one conversation first."));
})();
