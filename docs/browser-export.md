# Save your chats from the browser

The extension in `extension/` copies the one conversation you have open on Claude (claude.ai), ChatGPT (chatgpt.com, including chats inside projects and custom GPTs) or Grok (grok.com) into a file on your computer, only when you click. Nothing runs in the background, nothing reads your other chats, and nothing is sent anywhere except to the site you are on.

## Install
Chrome → `chrome://extensions` → switch on Developer mode → Load unpacked → choose the `extension` folder. Pin it from the puzzle-piece menu. After an update, press the round arrow on its card.

## Use
1. Open one conversation and click the Scriptorium button: a small window opens.
2. Export this conversation shows the site, title, message count and whether the copy is complete. A chat still being written, or whose earliest turns are missing, cannot be saved.
3. Save asks where to put the file the first time; choose your Scriptorium folder. Chrome remembers it.
4. Save attachments (shown when the chat has pictures or files) saves their originals in one zip beside the chat file, with `attachments.json` listing what was saved and what the site no longer has.

## Files
- `capture-<site>-<chat id>.json`: the chat (Scriptorium's small wrapper: site, chat id, title, complete, messages in order, each picture or file marked in place), plus the site's own full answer under `raw`. Saving the same chat again replaces this one file.
- `capture-<site>-<chat id>-attachments.zip`: the originals, when you ask for them.

## Write notes
`python3 engine/import_captures.py --root "$HOME/scriptorium"` reads these files and each site's official account export in the same folder (Claude's and ChatGPT's `conversations.json`, Grok's `prod-grok-backend.json`). A chat that arrives more than once, or from two sources, stays one record; only messages not seen before get notes, with the chat's earlier notes and its last ten turns given to the writer as context.

## Limits
Unofficial: not affiliated with Anthropic, OpenAI or xAI. It uses the same addresses the sites' own pages use, so a site change can stop it until the extension is updated; it then says so instead of saving a partial copy. Privacy: [../extension/PRIVACY.md](../extension/PRIVACY.md).
