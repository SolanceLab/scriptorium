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

Set `SCRIPTORIUM_PEN_STORE` to an importable `module:attr` naming an object, class, or zero-argument factory. The MCP server requires the five journal methods. It exposes each core, spine or marrow tool when the store implements the corresponding method below. The transfer command also requires the export and import methods. The default is the local-files store. Configure keys and writers as in [writers.md](writers.md).

Every operation is scoped to the supplied `writer`. The store must enforce that scope itself. Use `ValueError` for invalid input, missing individual records, or conflicts, and `OSError` for storage failures. MCP surfaces these errors to callers. Mutations that return `None` signal success by returning normally. Each write must be atomic, including the version check and the spine move on delete.

| Method | Arguments and success result | Method-specific errors |
| --- | --- | --- |
| `append(writer, entry)` | `entry` is a dictionary with a text `body` and optional text `title`. Append to the current local journal day and return the new id. | Invalid body or title. |
| `edit(writer, id, text)` | Replace the journal entry body, keeping its header and other entries. Return `None`. | Invalid or missing id, or invalid body. |
| `read(writer, id)` | Return a journal entry dictionary with `id`, `time`, `writer`, `body` and `title` if present. | Invalid or missing id. |
| `day(writer, date)` | Return the journal entries in append order for a `YYYY-MM-DD` date, or an empty list. | Invalid date. |
| `recent(writer, n)` | Return up to `n` journal entries, newest first, or an empty list. | `n` is not a positive integer. |
| `core_read(writer, version=None)` | Return a version dictionary for the latest version by default or the given positive integer. Its fields include `id`, `number`, `based_on`, `time`, `writer`, `length`, and `text`. | Invalid version or no matching version. |
| `core_write(writer, text, based_on)` | Append a new core version and return its integer number. `based_on` must equal the current latest number, or 0 for the first write. | Invalid text or base number, or stale base. |
| `core_history(writer)` | Return metadata dictionaries with `number`, `based_on`, `time`, `writer` and `length` in ascending version order, or an empty list. | Unreadable version data. |
| `spine_write(writer, title, body, pinned=False)` | Create an entry with text title and body and a Boolean pinned flag. Return the new id. | Invalid title, body or pinned flag. |
| `spine_edit(writer, id, title=None, body=None, pinned=None)` | Update supplied fields and the edit timestamp. `None` keeps the previous field value. Return `None`. | Invalid fields or invalid, missing or deleted id. |
| `spine_delete(writer, id)` | Retain the entry in deleted storage and remove it from active reads. Return `None`. | Invalid, missing or already-deleted id, or a conflicting archived entry. |
| `spine_read(writer, id=None)` | With an id, return one active entry with `id`, `writer`, `title`, `body`, `created`, `edited`, and `pinned`. With `None`, return a list with pinned entries first, newest creation time first within each group. An empty spine returns an empty list. | Invalid, missing or deleted explicit id. |
| `marrow_read(writer, version=None)` | Like `core_read`, with an additional `spine_ids` list. | Invalid version or no matching version. |
| `marrow_write(writer, text, based_on, spine_ids)` | Like `core_write`, with a list of spine id strings. The local store checks id format and retains the references; references can include deleted entries. | Invalid text, base or spine ids, or stale base. |
| `marrow_history(writer)` | Like `core_history`, for marrow. | Unreadable version data. |
| `export_pen(writer)` | Return a snapshot of all records, including deleted spine entries. Each dictionary has `kind` (journal, core, spine or marrow), `file` (the logical Markdown filename), `deleted` (Boolean), `header` (dictionary) and `body` (text). | Invalid records or records belonging to another writer. |
| `import_pen(writer, records)` | Copy an exported snapshot, preserving ids, fields, unknown header metadata, timestamps, journal order, version history and deletion state. Return `None`. Re-running with the same snapshot must skip identical records already copied. Protect concurrent writes. | Invalid records or a destination conflict. A storage failure may leave completed records in the destination. |

Local record ids are 32 lowercase hexadecimal characters. Local timestamps are ISO 8601 strings with a UTC offset. Legacy local versions without a saved `based_on` report the previous version number in reads and histories. The local importer accepts an empty writer namespace or an identical subset of the snapshot. It refuses conflicting or unrelated destination data before copying and commits each journal day as one file. A CLI retry takes a fresh snapshot, so leave already-copied source records and their journal days unchanged until the transfer completes. On failure the command exits nonzero and reports the matching records it could confirm at the destination by kind.
