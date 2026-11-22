# Journal storage and tools

A journal is what the AI writes in its own words, as its own record, and it can be named for any purpose: a journal, reflections, lessons, a summary. This store holds those entries. Each agent owns named journals (the store calls them collections). Configure the agent's key as described in [Agents](agents.md). On a key shared by several agents, every journal tool also takes a required `agent` argument. Chat extraction reserves the top-level `pen` folder in any casing and keeps its output elsewhere.

| Tool | Arguments and result |
| --- | --- |
| `journals` | Lists your journals with `collection`, active entry `count`, `by_day`, and the `fields` each declares. A journal you declared fields for and have not yet written in is listed with a count of 0. |
| `journal_write` | Supply `collection` and `body`, with optional `title`, `pinned` (default false) and `fields`. Returns `id`. The first write creates the journal and can set `by_day: true`. |
| `journal_edit` | Supply `id`, `based_on`, and any of `body`, `title`, `pinned` and `fields`. Returns the new `version`. Omitted values keep their values; `fields` merges into the entry's fields. A stale base is refused. |
| `journal_read` | Supply `id`. Returns the current version. With history switched on it also takes `version`. |
| `journal_list` | Supply `collection` and optionally `pinned_only`, `day` (`YYYY-MM-DD` for a `by_day` journal), `limit` (default 20), or `surface`. Returns active entries in order of creation, newest first. |
| `journal_history` | Listed and callable only with `SCRIPTORIUM_HISTORY=on`. Supply `id`. Returns full versions in order, oldest first. |
| `journal_delete` | Supply `id`. Moves the entry and its history aside and returns `ok: true`. |

The old names (`pen_collections`, `pen_write`, `pen_edit`, `pen_read`, `pen_list`, `pen_history`, `pen_delete`) still work when called, with the same arguments and the same rules. They are not listed.

<a name="entry-fields"></a>

## Entry fields

An entry is a title and a body unless you declare more. Each journal may declare its own fields in `SCRIPTORIUM_FIELDS`, a JSON object in the same shape as `SCRIPTORIUM_SILO`:

```sh
SCRIPTORIUM_FIELDS='{"*": {"journal": {"emotions": "list", "keynotes": "text", "mood": {"type": "number", "required": true}}}}'
```

- The first level is an agent name or `*`; the second is a journal name or `*`; then each field is a type, or `{"type": ..., "required": true}`. A named agent beats `*`, and a named journal beats `*`.
- Types: `text`, `list` (of text), `number` (not true or false, never NaN or infinity), `boolean`, and `object` (JSON values, valid UTF-8).
- `journal_write` and `journal_edit` take `fields`. A field the journal does not declare is refused, and the answer names the ones it allows. A journal with no declaration takes none.
- The values are stored with each version of the entry under one key, `fields`, which is left out when empty. `journal_read` and `journal_list` return it.
- `journal_edit` merges `fields` into the entry's current ones. A null clears an optional field. A required field must be given on write and cannot be cleared.
- Changing the declaration later never breaks reading old entries; only the fields sent with a write or edit are checked.
- Your AI finds the declared fields in the answer of `journals`.

## History

The store keeps every version of every entry, and a delete only sets the entry aside. By default the AI cannot see any of that: `journal_read` has no `version` argument, and `journal_history` and `pen_history` are neither listed nor callable. This keeps the AI from reading replaced wording as current. `SCRIPTORIUM_HISTORY` is unset, `off` or `on`; any other value stops the server from starting.

With `SCRIPTORIUM_HISTORY=on`, `journal_history` is listed, and every older version it returns (or that `journal_read` returns for a `version`) carries `replaced_on` (when the next version was written) and `current: false`. These two keys appear only in the answer; they are never written to the stored versions.

Recovering a deleted entry is something you do, not a tool (see below). It is done on the local store; recovery on the cloud store is not part of this release.

## Reading and sharing

The read tools (`journal_read`, `journal_list`, `journal_history`, and `journals`) take an optional `of` with the exact configured agent name. Omit it to read your own notebooks. To read someone else's, use their name and the notebook must be in the pool. A private notebook is omitted from the collection list. Requests for a private or absent notebook or entry get the same “not found” result. Writes, edits, and deletes are scoped to the acting agent. See [Notebook sharing and surfaces](agents.md#notebook-sharing-and-surfaces) for the settings.

Each version records the `surface` of the key that wrote it, when the key has a label. Editing with an unlabelled key creates an unlabelled version. `journal_list(surface=...)` matches the current version's surface before applying the limit.

An entry has an id, journal (collection), optional title, body, optional `fields`, creation and edit timestamps, pinned flag, writer, and version number. The collection's day grouping is set on its first write. Set `SCRIPTORIUM_PEN_TZ` to an IANA time zone to set the calendar day at creation. The default is the system local time zone. The original creation day stays with an entry across edits and transfers.

The default store keeps each entry at `pen/<writer>/<collection>/<id>/`. Each version is one numbered Markdown file with a JSON header on the first line and the body below it. The latest file is the current entry and earlier versions sit beside it. A deleted entry's directory moves under the collection's `.deleted/` folder. To restore it in the local store, stop the server and move the id directory back into its collection. An empty collection with deleted entries still appears in the collection list.

## Transfer

Start with an empty destination for the writer. Keep the destination offline until the transfer verifies the full snapshot.

```sh
python3 engine/pen.py transfer --from local:SOURCE --to local:DESTINATION --writer first
```

Either store may be an importable `module:attr`. The destination requires [PenStore contract version 3](your-own-store.md#pen-store) to preserve notebook defaults. Entries from a legacy source without notebook metadata transfer with a private default. Transfer verifies every entry, including deleted entries, all versions in order, timestamps, bodies, surface labels, extra header fields, and each notebook's stored sharing default. Configure sharing and read rules at the destination before serving it. The destination uses its own settings. If interrupted, keep the source unchanged and run the same command again. It skips verified entries already copied and refuses conflicting destination data.
