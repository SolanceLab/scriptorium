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

<a name="pen-store"></a>

## Entry store

Set `SCRIPTORIUM_PEN_STORE` to an importable `module:attr` naming a store instance, a class, or a zero-argument factory. The MCP server requires all seven entry methods below. The instance must declare `contract_version = 3` for MCP; a store that declares 2 still starts, but cannot be used with `SCRIPTORIUM_FIELDS`. The server checks a store by these seven methods (`collections`, `write`, `edit`, `read`, `list`, `history`, `delete`), not by tool names. The transfer command uses `export_snapshot` and `import_snapshot`. The default implementation is `engine.pen.LocalFilesPenStore`. Configure keys as described in [Writers](writers.md).

Every method must enforce its supplied `writer` scope. Raise `ValueError` for invalid input, missing individual entries or versions, and conflicts. Raise `OSError` for storage failures. MCP surfaces these errors to callers. The parameter names below are also the keyword names used by MCP.

| Method | Contract |
| --- | --- |
| `collections(writer, *, visible=None)` | Return a list of dictionaries with `collection` (name), `count` (active entries), and `by_day` (Boolean). Include collections whose entries are all deleted. |
| `write(writer, collection, body, title=None, pinned=False, by_day=None, *, surface=None, fields=None)` | Return a new id (32 lowercase hexadecimal characters). Create the collection on first write. Default to a flat collection and version 1, with `created` and `edited` set to the same timestamp. An omitted `by_day` inherits an existing collection's mode. Refuse a conflicting explicit mode. |
| `append(writer, collection, body, *, id=None, day=None, fields=None, title=None, surface=None, on_create=None)` *(optional)* | Atomically find-or-create and append in one step (under a writer lock or inside a single database transaction — never emulated by read-then-edit). With `id`, append to that active entry in `collection`; without `id`, append to the newest active entry for `day` (defaulting to today in the store's time zone) in a `by_day` collection, creating it (`title` or `day`) if absent, and refusing if the collection is not `by_day`. When creating a new entry, call `on_create()` (if supplied) inside the writer lock or transaction before inserting anything; if it raises `ValueError`, abort without creating the entry (do not call `on_create` when appending to an existing entry). Declare `on_create=None` explicitly in the signature rather than via `**kwargs`: when a journal declares required fields and `append` does not explicitly declare `on_create`, the server fails closed and requires all required fields on every `append` without `id`. Join `body` with a blank line; merge `fields` (lists append new items in order without duplicates, objects merge key by key, text/number/boolean replace when given); stamp `surface`. History-keeping stores record a new version; `keeps_history = False` stores replace the current entry. Return `{"id": id, "version": version, "action": "appended" \| "created"}`. Stores that omit `append` work normally, and `journal_write(..., append=true)` is refused with a plain message. |
| `edit(writer, id, based_on, body=None, title=None, pinned=None, *, surface=None, fields=None)` | Require the current version as a positive integer. In a single atomic operation, check that base and append the next version. Return its number. Preserve earlier versions, unchanged fields, unknown header fields, and the original creation time. Set `edited` to the edit time. |
| `read(writer, id, version=None, *, visible=None)` | Return a version dictionary. Default to the latest. Refuse missing or deleted entries and missing versions. |
| `list(writer, collection, pinned_only=False, day=None, limit=20, *, surface=None, visible=None)` | Return current version dictionaries of active entries sorted by creation time, newest first, with optional pin and calendar-day filters. Filter before applying the positive integer limit. The day filter uses the recorded creation date and requires a day-grouped collection. A missing collection returns an empty list. |
| `history(writer, id, *, visible=None)` | Return full version dictionaries in ascending version order, including the current version. Refuse missing or deleted entries. |
| `delete(writer, id)` | Atomically move the full entry and its history into recoverable storage. Return `None`. Deleted entries are unavailable through entry tools. |
| `export_pen(writer)` | Return a consistent snapshot as a list of dictionaries with `collection`, `id`, `deleted` (Boolean), and `versions` (ordered list of full version dictionaries). Entry order may vary by store. Include deleted entries and unknown version fields. |
| `import_pen(writer, records, *, metadata=None)` | Validate the snapshot and the destination's existing entries before copying. Accept an empty destination or an identical subset, and reject conflicts and duplicate ids. Copy each missing entry with its entire history atomically. An interrupted import may leave a complete subset, so a retry of the same snapshot can skip it. Return `None` on success. |

For reads, `writer` is the owner and `visible`, when supplied, is a predicate taking `(collection, stored_default)`. Apply it under the same lock or transaction as the read. Newly created notebooks get a stored default of `pool` atomically with their first entry. Pre-existing notebooks without metadata have a default of `silo`. For read and history, resolve the active entry's collection and check the predicate before fetching version data. Raise `ValueError("entry not found")` for hidden and missing entries and `ValueError("collection not found")` for hidden and missing collections in list. Omit hidden collections from collections entirely.

For write and edit, `fields` is passed only when there is something to store: a non-empty object of JSON values, kept under the one header key `fields` of the new version (left out when empty). The server has already checked the values against the journal's declaration. On edit, merge `fields` into the current ones, where a null removes a field, and omit the key when nothing is left. `surface` is supplied by the authenticated server. Each new version has that string or omits the field when `surface` is `None`. Earlier versions are preserved. For list, a supplied surface matches the current version before applying the limit.

`export_snapshot(writer)` returns `{"version": 1, "collections": {"notebook": "pool"}, "entries": [...]}`, with each collection's stored default and all the entry records from the same read. `import_snapshot(writer, snapshot)` validates the metadata and installs it before entries. A retry accepts identical metadata and entries already present. The supplied `PenStore.import_snapshot` implements this via `import_pen(writer, records, metadata=...)`. The transfer command verifies the metadata and all entries after import. The source and destination settings are separate from the snapshot.

Each version dictionary has `id`, `collection`, `writer`, `body` (text), `created` and `edited` (ISO 8601 timestamps with offsets), `pinned` and `by_day` (Booleans), and `version` (integer). It may also have `title` and `surface` (text) and additional JSON-compatible header fields. The writer, id, collection, creation timestamp, and day grouping are preserved in every version. The transfer command compares the destination snapshot with the source after import, including version order and all fields.

### Cloud store seam (`SCRIPTORIUM_STORE`)

To run the Cloudflare Worker (`cloud/entry.py`) in front of your own database or API instead of the built-in Durable Object `SqlPenStore`:

1. Bundle one store module into the Worker folder with `python3 cloud/build.py --store path/to/my_store.py`. The build copies only that single `.py` file and refuses symlinks, directories, invalid module names, files inside the output folder, or filenames that collide with built-in Worker files (`entry.py`, `server.py`, `engine.py`).
2. Set `SCRIPTORIUM_STORE = "my_store:MyStore"` in the Worker environment. Leaving `SCRIPTORIUM_STORE` unset keeps the default Durable Object store.
3. After authenticating the request, the Worker constructs `MyStore(env)` with the Worker `env` (so your store can read its own secrets and variables) and checks the v3 `PenStore` contract (`contract_version = 3` and the required methods). Store methods on the Worker may be `async def` (for example, when calling `fetch()` against Supabase or an HTTP API). An invalid store or failed initialization returns HTTP 503 with a generic error (initialization exception details are not exposed to callers).

### Retry safety on custom cloud stores (`mutate`)

The built-in Durable Object store stores `X-Request-Id` replays in its own SQLite transaction. A custom cloud store may implement an optional method (sync `def` or `async def`; required to be `async def` when any store method is `async def`) to provide atomic retry safety on its own backend:

```python
async def mutate(self, request_id, fingerprint, apply):
    ...
```

- `request_id` is an opaque string scoped by the Worker to `(writer, X-Request-Id)` so two writers using the same `X-Request-Id` header never collide or learn about each other's retries.
- `fingerprint` is a SHA-256 hex digest of the canonical write request (`name`, `arguments`, acting `agent`, and `surface` when set).
- `apply` is a zero-argument callable (an `async def` callable when `mutate` is `async def`, so call `await apply()`) that runs the write and returns the JSON reply string on success, or `None` when the write failed (so failures are never saved).

Inside `mutate`, check the writer-scoped `request_id` atomically with the write:

- If `request_id` was already saved with the same `fingerprint`, return the saved reply string without calling `apply()`. The Worker rewrites the JSON-RPC `id` to match the current request.
- If `request_id` was already saved with a different `fingerprint`, raise `RequestIdConflict` (from `engine.pen`; the Worker returns HTTP 409 with a fixed public message and never forwards exception text).
- Otherwise call `reply = apply()` (or `reply = await apply()`). If `reply` is not `None`, save `(request_id, fingerprint, reply)` atomically with the write and return `reply`. If `reply` is `None`, save nothing and return `None`.

If a custom store does not implement `mutate`, the Worker refuses `X-Request-Id` on write calls with HTTP 400 and a plain error message rather than pretending writes are retry-safe.

### Current-entry-only stores (`keeps_history = False`)

By default (`keeps_history = True`), a store keeps every version of an entry and moves deleted entries aside for recovery. A store backed by a table or API that keeps only the current row may declare `keeps_history = False` on the class (or instance):

- `journal_history` (and `pen_history`) is never listed or callable. Setting `SCRIPTORIUM_HISTORY=on` with a `keeps_history = False` store refuses to start (both locally and in the cloud).
- `journal_read` never takes a `version` argument and never returns an older version.
- `edit` replaces the entry in place (returning the new version number) rather than preserving earlier versions.
- `delete` removes the entry permanently (no set-aside).
- `history()` does not need to be implemented when `keeps_history = False`.

Default stores (`keeps_history = True`, or attribute omitted) keep today's versioned and set-aside behaviour unchanged.
