# Plug in your own store

Keep your messages somewhere other than a chat export, or want the facts saved into your own database? Swap in your own code for reading and saving:

```
python3 engine/nightly.py --out state --source my_store:source --sink my_store:sink
```

- **source** (`read(cursor)`, `cards()`): hands Scriptorium the messages newer than the last run, and your person cards.
- **sink** (`write(facts, suggestions)`): saves what was found and says how many were stored, skipped (already there), rejected (with a reason) or deferred (try again later).
- From your own Python code, call `run_roster(source, sink, state_dir, model_cmd=...)` in `engine/roster.py`.

The runner keeps you safe on bad nights:

- **Settle window.** Messages newer than a few minutes wait for the next run (10 minutes in `run_roster`; `--settle-minutes` in `nightly.py`).
- **Per-person cap.** At most 60 messages per person are read in one run (60 in `run_roster`; `--per-person-cap` in `nightly.py`); the rest wait for the next run and the run's notes say so. Nothing is dropped.
- **One run at a time**, by a lock.
- **The cursor only moves on a clean run**: no person failed, every message had a time, and your sink accounted for every item and deferred nothing. A crashed run is simply read again; your sink counts the repeats as skipped.
- **Nothing silent.** `status.json` records every run; `rejected.jsonl` keeps every item turned away, by Scriptorium's checks or by your sink, with the reason.

## Account ids

If your messages carry the author's account id (`sender_id`), a card can list `ids` (accounts that are this person alone) and `shared_ids` (accounts that speak for several people, such as one bot voicing several characters):

- a personal id ties the message to its one card, whatever name it shows;
- a shared id is matched by the shown name, among the cards that list it only;
- an id that no card knows ties the message to no one, because a shown name can be anyone's.

Ids are used for matching only and are never sent to the model. The exact shapes are in `CONTRACT.md`.

## Pen store

Set `SCRIPTORIUM_PEN_STORE` to an importable `module:attr` naming a store instance, a class, or a zero-argument factory. The MCP server requires all seven entry methods below. The transfer command uses `export_pen` and `import_pen`. The default implementation is `engine.pen.LocalFilesPenStore`. Configure keys as described in [Writers](writers.md).

Every method must enforce its supplied `writer` scope. Raise `ValueError` for invalid input, missing individual entries or versions, and conflicts. Raise `OSError` for storage failures. MCP surfaces these errors to callers. The parameter names below are also the keyword names used by MCP.

| Method | Contract |
| --- | --- |
| `collections(writer)` | Return a list of dictionaries with `collection` (name), `count` (active entries), and `by_day` (Boolean). Include collections whose entries are all deleted. |
| `write(writer, collection, body, title=None, pinned=False, by_day=None)` | Return a new id (32 lowercase hexadecimal characters). Create the collection on first write. Default to a flat collection and version 1, with `created` and `edited` set to the same timestamp. An omitted `by_day` inherits an existing collection's mode. Refuse a conflicting explicit mode. |
| `edit(writer, id, based_on, body=None, title=None, pinned=None)` | Require the current version as a positive integer. In a single atomic operation, check that base and append the next version. Return its number. Preserve earlier versions, unchanged fields, unknown header fields, and the original creation time. Set `edited` to the edit time. |
| `read(writer, id, version=None)` | Return a version dictionary. Default to the latest. Refuse missing or deleted entries and missing versions. |
| `list(writer, collection, pinned_only=False, day=None, limit=20)` | Return current version dictionaries of active entries sorted by creation time, newest first, with optional pin and calendar-day filters. Filter before applying the positive integer limit. The day filter uses the recorded creation date and requires a day-grouped collection. A missing collection returns an empty list. |
| `history(writer, id)` | Return full version dictionaries in ascending version order, including the current version. Refuse missing or deleted entries. |
| `delete(writer, id)` | Atomically move the full entry and its history into recoverable storage. Return `None`. Deleted entries are unavailable through entry tools. |
| `export_pen(writer)` | Return a consistent snapshot as a list of dictionaries with `collection`, `id`, `deleted` (Boolean), and `versions` (ordered list of full version dictionaries). Entry order may vary by store. Include deleted entries and unknown version fields. |
| `import_pen(writer, records)` | Validate the snapshot and the destination's existing entries before copying. Accept an empty destination or an identical subset, and reject conflicts and duplicate ids. Copy each missing entry with its entire history atomically. An interrupted import may leave a complete subset, so a retry of the same snapshot can skip it. Return `None` on success. |

Each version dictionary has `id`, `collection`, `writer`, `body` (text), `created` and `edited` (ISO 8601 timestamps with offsets), `pinned` and `by_day` (Booleans), and `version` (integer). It may also have `title` (text) and additional JSON-compatible header fields. The writer, id, collection, creation timestamp, and day grouping are preserved in every version. The transfer command compares the destination snapshot with the source after import, including version order and all fields.
