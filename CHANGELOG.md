# Changelog

## 1.1.1

Fix: the Codex server file is now `plugin/codex.mcp.json`. 1.1.0 named it `plugin/.mcp.json`, which Claude Code also reads and rejects (a server with an address and no type). Codex behaves as before: same address, same `SCRIPTORIUM_MCP_KEY`. README: a Use in Codex section.

## 1.1.0

Works in Codex too: the plugin carries a Codex manifest (`plugin/.codex-plugin/plugin.json`) and server file (`plugin/.mcp.json`); the server key comes from the `SCRIPTORIUM_MCP_KEY` environment variable. The changelog's earlier "Unreleased" notes are folded into 1.0.0, where they shipped. The copyright line at the top of each file now names Scriptorium and its licence (PolyForm Noncommercial 1.0.0); the 1.0.0 line wrongly reserved every right, which the licence does not.

## 1.0.0

First public release: the memory writer (exact words, source lines, real dates read off a calendar, salience, valence, emotions; every memory checked against its lines), people cards, and a local MCP server.

A new Claude Code session says in one line when a newer plugin is out (at most once a day, sends nothing about you, off with `PLUGIN_UPDATE_CHECK=0`); README Updates section with the auto-update steps.

Optional nightly roster fill: `nightly.py` reads new messages and keeps checked facts and field suggestions per person (owner names are a setting), `review.py` accepts or dismisses the field suggestions; cron and launchd examples included.
