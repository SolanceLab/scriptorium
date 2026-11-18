# The pen

The pen keeps hand-written records the AI writes itself. It is never filled from chat exports. It includes the journal, core, spine and marrow.

Configure writers as described in [writers.md](writers.md). Journal tools write, edit, read by id or date, and list recent entries. The default store keeps a Markdown file per day under `pen/<writer>/journal/`. Set `SCRIPTORIUM_PEN_TZ` to an IANA time zone for journal days; otherwise the system local zone is used. Each entry has an id, offset timestamp and writer in its header. Extraction reserves the top-level `pen` directory name in any casing.

Core and marrow are numbered versions under `pen/<writer>/core/` and `marrow/`. Read the latest or a numbered version; history lists number, base version, time, writer and text length. Write with `text` and `based_on`, using 0 for the first version. A stale `based_on` is refused. Marrow writes also take `spine_ids`.

Spine tools write a title and body with an optional pinned flag, edit those fields, read one id or list all entries with pinned entries first, and delete. Deleting moves the file to `spine/.deleted/` so it can be recovered.

Transfer one writer through the store interface. Start with an empty destination for that writer.

```sh
python3 engine/pen.py transfer --from local:SOURCE --to local:DESTINATION --writer first
```

Either store may instead be an importable `module:attr` implementing the pen store contract. Transfer includes deleted spine records and all versions. If a transfer fails, keep the source unchanged and run the same command again. It skips matching data already copied and refuses conflicting destination data.
