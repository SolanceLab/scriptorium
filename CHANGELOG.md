# Changelog

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
