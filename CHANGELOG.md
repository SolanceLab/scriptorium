# Changelog

## Unreleased

The public notes prompt asks for named speakers and citations for every supporting line, including the reply that settles a claim. Configure speaker names through CLI flags or the server environment. The writer can recover a missing citation for a fragment that matches one message in the same model call and records the added IDs in the notes JSON. These changes affect newly written files only.

## 1.6.0

Two new options, neither of them a default.

The pen in the cloud: run your AI's pen on your own free Cloudflare account instead of this computer, so it can write from anywhere, any time (docs/cloud.md). Your entries live only in your account. It is the same pen with the same seven tools and the same agent rules; local stays exactly as it was. Writes can carry an `X-Request-Id`, so a retried write is saved once. Keys can overlap while you replace one. Moving a whole pen between the cloud and this computer is not in this release.

Notes on your API key: `write_notes --api` sends each chat straight to the Anthropic API and bills `ANTHROPIC_API_KEY`. The default still runs through your Claude Code login, and now says so when an API key is set, because the default never passes your key on. With `--api`, a failed attempt that was billed still counts in the cost total, and errors never show the key. The notes writer's hidden instruction now says notes, not memories.

## 1.5.0

The chat reader's output is now called notes. Only your AI writes its journal, by hand, with the pen; what the reader pulls from your chats are notes, each proved by its line. New tool name `write_notes` and new file `engine/write_notes.py`. `write_memories` still works, as a tool and as `engine/write_memories.py` (importing or running it gives `write_notes`), so nothing you run needs to change; the skill falls back to `write_memories` on an older server. The notes files do not change: the field that holds the notes keeps its old name, `memories`, so files you already have and readers such as Antechamber keep working. README, docs and the skill now lead with the journal. The README states the key rules as they are: bind every key to its agent, even with one agent; a shared key opens every pen on it; a key not bound to any agent uses only the assistant.

## 1.4.0

Shared keys: one key can now belong to several agents (an agent is the role or the identity a model speaks as). On such a key every pen tool takes a required `agent` argument, one of the agents on the key, spelled exactly; each agent's entries stay separate. A key with one agent works exactly as in 1.3.0. New `SCRIPTORIUM_AGENTS` setting (docs/agents.md); `SCRIPTORIUM_WRITERS` still works and now also accepts lists. `MEMORY_ENTRY_WRITERS` counts too: setting either of them together with `SCRIPTORIUM_AGENTS` is refused. A settings value of `null`, a list, a string or a number is now refused at start (1.3.0 let `null` through as extraction only). New `engine/setup_agents.py` writes the settings file; `mcp/count_tools.py` measures what a client sees. The server prints the port it really listens on, so `--port 0` works. Anyone holding a shared key can read, write, edit and delete entries for every agent on it.

## 1.3.0

The pen: your AI can now keep records of its own, written by hand. A writer owns named collections of entries (a journal, reflections, lessons, a summary, whatever it chooses). Every entry keeps its version history, can be pinned, and a delete sets it aside with its history instead of erasing it; a collection can group its entries by day. Seven tools: `pen_collections`, `pen_write`, `pen_edit`, `pen_read`, `pen_list`, `pen_history`, `pen_delete` (docs/pen.md).

Writers: each server key belongs to exactly one writer, so an AI can only read and write its own pen, whatever a tool call says (docs/writers.md). The pen is never filled from chat exports: the memory and people tools cannot reach it, and tests prove it. Your own store: the pen has its own `PenStore` seam (docs/your-own-store.md), and `python3 engine/pen.py transfer` moves a writer's whole pen between stores, resuming without duplicates if it stops halfway.

A new skill, pen-writing, tells the AI how to write entries it can find again later. Works on Windows too (file locking falls back to msvcrt).

## 1.2.2

The People page shows the facts the nightly run set aside to check by hand, in its Suggestions box, with the reason and the words they came from. Accept adds the fact to the person's facts; Dismiss drops it; the choice is kept in `held_decided.json` so it never comes back. `review.py` has the same as commands (`held`, `keep-held ID`, `drop-held ID`). A host's own API may send these as suggestions of `kind: 'fact'` (dashboard/HOST.md).

The People page's own tests now run on every push, and `package-lock.json` is in the repository so they install the same versions each time. README: a People page section on the front page.

## 1.2.1

Fix: times with a short fraction of a second, such as `2026-11-16T22:24:42.02+07:00`, are now read on Python 3.9 and 3.10. Postgres drops trailing zeros from the fraction, and those Python versions read only 3 or 6 digits, so a message like that counted as having no time: the roster run failed and its cursor stayed put every night. The README has always said Python 3.9 or newer, and the Python that ships with macOS is 3.9.

## 1.2.0

Matching people to their own messages: a message is now tied to the person who wrote it when the sender's name is the person's first name, or the same name in a different letter style, as long as only one card fits; a name two cards share never ties a message to either. Account ids: a message may carry `sender_id`, and a card `ids` and `shared_ids`, so an author is matched by account before name (see CONTRACT.md). The roster step takes a prompt file of your own (`--prompt`, `--found-prompt`), and the server reports the plugin's version.

Your own store: `nightly.py` can read from and save to your own code (`--source`, `--sink`) through a new `engine/roster.py`. The runner adds a settle window, a per-person cap that holds the rest for the next run, a lock, a `status.json` for each run, a `rejected.jsonl` for anything turned away with its reason, and a cursor that moves only when the run was clean. Without those flags `nightly.py` behaves as before and now also writes `status.json` and `rejected.jsonl` beside its results.

README: a short front page with a drawing of how it works (and how its pair Antechamber finds it all again) and a "What's coming next" section; the full detail moved to `docs/` (codex, roster, your own store, limits, how it works). The quick start now says to clone the repository first, which the server needs.

## 1.1.1

Fix: the Codex server file is now `plugin/codex.mcp.json`. 1.1.0 named it `plugin/.mcp.json`, which Claude Code also reads and rejects (a server with an address and no type). Codex behaves as before: same address, same `SCRIPTORIUM_MCP_KEY`. README: a Use in Codex section.

## 1.1.0

Works in Codex too: the plugin carries a Codex manifest (`plugin/.codex-plugin/plugin.json`) and server file (`plugin/.mcp.json`); the server key comes from the `SCRIPTORIUM_MCP_KEY` environment variable. The changelog's earlier "Unreleased" notes are folded into 1.0.0, where they shipped. The copyright line at the top of each file now names Scriptorium and its licence (PolyForm Noncommercial 1.0.0); the 1.0.0 line wrongly reserved every right, which the licence does not.

## 1.0.0

First public release: the memory writer (exact words, source lines, real dates read off a calendar, salience, valence, emotions; every memory checked against its lines), people cards, and a local MCP server.

A new Claude Code session says in one line when a newer plugin is out (at most once a day, sends nothing about you, off with `PLUGIN_UPDATE_CHECK=0`); README Updates section with the auto-update steps.

Optional nightly roster fill: `nightly.py` reads new messages and keeps checked facts and field suggestions per person (owner names are a setting), `review.py` accepts or dismisses the field suggestions; cron and launchd examples included.
