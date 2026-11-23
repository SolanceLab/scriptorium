# Scriptorium Export (browser extension)

Unofficial. Not affiliated with OpenAI, Anthropic or xAI. Copies the one conversation you have open, only when you click, into a file on your computer. Nothing is sent anywhere else.

It works on three sites: claude.ai, chatgpt.com and grok.com. Open one conversation, click the toolbar button (a small Scriptorium Export window opens for that tab; click again and it is brought forward), press Export, check the preview (site, title, message count, Complete or Incomplete), then press Save. Chrome asks where to save once; pick your Scriptorium folder and it remembers. The file is named `capture-<site>-<id>.json`, the name `scriptorium import` reads. The text file stays words only: each image or file in the chat is marked in place (for example `[image: generated · 1]`).

**Save attachments (N)** appears after an export that found images or files. It fetches this chat's own items as originals, one after another, into one zip beside the chat file: `capture-<site>-<id>-attachments.zip` (`attachments.json` lists each item and whether it was saved or missing; the files are under `files/`). Items that cannot be fetched are listed as missing; the rest are still saved. The importer ignores the zip. Grok attachments cannot be saved yet.

An Incomplete copy (the chat is still being written, its earliest turns are missing, or the site changed its format) cannot be saved; the window says why. On any page that is not a single conversation it says "Open one conversation first."

## Install (Load unpacked)

1. Open `chrome://extensions` and switch on Developer mode.
2. Press Load unpacked and choose this `extension/` folder.
3. Pin the Scriptorium Export button to the toolbar.

## What it will never do

- List or page through your other conversations. One click reads the one open conversation.
- Call any host but the page you are on. No analytics, no remote code, no background network calls.

See [PRIVACY.md](PRIVACY.md).

## Status and risks

- Claude and ChatGPT read the site's own conversation endpoint from your logged-in page (the same one the site's page uses). Those endpoints are not public APIs and can change; the reader then marks the file Incomplete with "this site changed; update the extension".
- **Grok: verify live before release.** Grok's endpoint is not confirmed. The reader understands the shape of Grok's official export, and fetches the request Grok's own page already made for the open conversation. If the page has not made one, the window says so.
- Streaming detection is limited to what each site marks on a message; the field names are unverified live.
- Saving uses `chrome.downloads` with the Save As dialog, from the export window itself: the window makes the file, hands it to Chrome, and releases it when the download ends. Keep the window open until it says Saved, Cancelled or the error; closing it early may cut the download. The extension holds no background state. Verify live before release.

- Save attachments is unverified live: ChatGPT's download endpoint and Claude's file addresses are taken from the shapes seen in test exports, and a signed download address on another host may be blocked by that host's CORS rules.

## Tests

`make test-extension` (or `node --test extension/test/*.test.mjs`; Node 22+). No npm packages. `make test` runs the Python side of the same fixtures.

Prior art (reading approach only; no code copied): bensquire/claude-exporter and pionxzh/chatgpt-exporter (MIT).
