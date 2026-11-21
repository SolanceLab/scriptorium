# The pen

The pen holds entries the AI writes by hand. Each agent owns named collections. Configure the agent's key as described in [Agents](agents.md). On a key shared by several agents, every pen tool also takes a required `agent` argument. Chat extraction reserves the top-level `pen` folder in any casing and keeps its output elsewhere.

| Tool | Arguments and result |
| --- | --- |
| `pen_collections` | Lists your collections with `collection`, active entry `count`, and `by_day`. |
| `pen_write` | Supply `collection` and `body`, with optional `title` and `pinned` (default false). Returns `id`. The first write creates the collection and can set `by_day: true`. |
| `pen_edit` | Supply `id`, `based_on`, and any of `body`, `title`, and `pinned`. Returns the new `version`. Omitted fields keep their values. A stale base is refused. |
| `pen_read` | Supply `id` and optionally `version`. Returns the requested version, defaulting to the latest. |
| `pen_list` | Supply `collection` and optionally `pinned_only`, `day` (`YYYY-MM-DD` for a `by_day` collection), `limit` (default 20), or `surface`. Returns active entries in order of creation, newest first. |
| `pen_history` | Supply `id`. Returns full versions in order, oldest first, starting at version 1. |
| `pen_delete` | Supply `id`. Moves the entry and its history aside and returns `ok: true`. |

The four read tools (`pen_read`, `pen_list`, `pen_history`, and `pen_collections`) take an optional `of` with the exact configured agent name. Omit it to read your own notebooks. To read someone else's, use their name and the notebook must be in the pool. A private notebook is omitted from the collection list. Requests for a private or absent notebook or entry get the same “not found” result. Writes, edits, and deletes are scoped to the acting agent. See [Notebook sharing and surfaces](agents.md#notebook-sharing-and-surfaces) for the settings.

Each version records the `surface` of the key that wrote it, when the key has a label. Editing with an unlabelled key creates an unlabelled version. `pen_list(surface=...)` matches the current version's surface before applying the limit.

An entry has an id, collection, optional title, body, creation and edit timestamps, pinned flag, writer, and version number. The collection's day grouping is set on its first write. Set `SCRIPTORIUM_PEN_TZ` to an IANA time zone to set the calendar day at creation. The default is the system local time zone. The original creation day stays with an entry across edits and transfers.

The default store keeps each entry at `pen/<writer>/<collection>/<id>/`. Each version is one numbered Markdown file with a JSON header on the first line and the body below it. The latest file is the current entry and earlier versions sit beside it. A deleted entry's directory moves under the collection's `.deleted/` folder. To restore it in the local store, stop the server and move the id directory back into its collection. An empty collection with deleted entries still appears in the collection list.

## Transfer

Start with an empty destination for the writer. Keep the destination offline until the transfer verifies the full snapshot.

```sh
python3 engine/pen.py transfer --from local:SOURCE --to local:DESTINATION --writer first
```

Either store may be an importable `module:attr`. The destination requires [PenStore contract version 2](your-own-store.md#pen-store) to preserve notebook defaults. Entries from a legacy source without notebook metadata transfer with a private default. Transfer verifies every entry, including deleted entries, all versions in order, timestamps, bodies, surface labels, extra header fields, and each notebook's stored sharing default. Configure sharing and read rules at the destination before serving it. The destination uses its own settings. If interrupted, keep the source unchanged and run the same command again. It skips verified entries already copied and refuses conflicting destination data.
