# Changelog

## Unreleased

A new Claude Code session says in one line when a newer plugin is out (at most once a day, sends nothing about you, off with `PLUGIN_UPDATE_CHECK=0`); README Updates section with the auto-update steps.

Optional nightly roster fill: `nightly.py` reads new messages and keeps checked facts and field suggestions per person (owner names are a setting), `review.py` accepts or dismisses the field suggestions; cron and launchd examples included.

## 1.0.0

First public release: the memory writer (exact words, source lines, real dates read off a calendar, salience, valence, emotions; every memory checked against its lines), people cards, and a local MCP server.
