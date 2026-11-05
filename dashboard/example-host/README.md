<!-- Scriptorium — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab) -->
# Example host

A small page that shows the People page over your own files, on your own computer. It is also the smallest working example of a host (see `../HOST.md`).

Two commands (run `npm install` once first):

```
node dashboard/example-host/build.mjs
node dashboard/example-host/serve.mjs --cards people --out found
```

Or in one step: `make people-page CARDS=people OUT=found`. Then open http://127.0.0.1:8788/ (`--port` changes it).

`--cards` is the folder of `person-<key>.json` cards (made by `write_people.py`). `--out` is the folder the nightly run writes to (`facts/`, `suggestions.json`, `fields/`).

What works here:

- The roster lists your cards, with the values you accepted from the nightly run (`fields/<key>.json`) merged in.
- Suggestions: Accept and Dismiss run `engine/review.py`, the same command you could run by hand, with its lock.
- Found in chats: facts list per person; Edit changes a fact's text (marked edited), Remove deletes it.

What does not: adding, editing or removing a person, and relations. Those happen in your card files in this version, so the page hides those buttons (`capabilities` in `../HOST.md`).

How it connects: `serve.mjs` uses only Node's standard library. It serves the page and a small `/api`, listens on this computer only, refuses requests that name another host, and refuses writes that come from another site.

Files: `main.jsx` (sets up the page with `adapters/local-files.js`), `build.mjs` (bundles with esbuild), `serve.mjs` (static files and `/api`).
