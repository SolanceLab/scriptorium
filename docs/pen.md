# The pen

The pen keeps hand-written records the AI writes itself. It is never filled from chat exports. The journal is available now; core, spine and marrow are planned for the same release.

Configure writers as described in [writers.md](writers.md). Journal tools write, edit, read by id or date, and list recent entries. The default store keeps a Markdown file per day under `pen/<writer>/journal/`. Set `SCRIPTORIUM_PEN_TZ` to an IANA time zone for journal days; otherwise the system local zone is used. Each entry has an id, offset timestamp and writer in its header.
