# Scriptorium contract

## Doors it offers

| Door | What it answers |
|---|---|
| `write_memories` (MCP tool, and `engine/write_memories.py`) | A chat export in; one memory file per conversation out |
| `write_people` (MCP tool, and `engine/write_people.py`) | The same export and its memory files in; one card file per recurring person out |
| `POST /mcp` on 127.0.0.1 (`mcp/server.py`) | MCP over HTTP, JSON responses, keyed |

## Data it owns

| Table / bucket / file | What is in it |
|---|---|
| `<out>/<conversation id>.json` | `conversation_id`, `title`, `session_date`, `memories` (topic, summary, excerpt, source_line_ids, source_session_date, salience, valence, emotions, context_tags), `rejected` (each with its reason), `calls` (model, usage, cost), `writer` (command, version, prompt fingerprint) |
| `<out>/person-<key>.json` | The same file shape, `conversation_id` = `person:<key>`; each memory is one aspect of the person with every fact and its date |

The memory file shape is the contract with any reader: a recall engine reads `conversation_id`, `memories[].summary`, `excerpt`, `source_line_ids` and `source_session_date`, and finds the source lines in the same export.

## Settings and secrets it needs

Named here, never valued.

| Name | What it is |
|---|---|
| `SCRIPTORIUM_MCP_KEY` | The key every request to the server must carry (at least 16 characters) |
| `--root` | The one folder the server may read and write |
| `SCRIPTORIUM_MODEL_CMD` | Optional: a model command of your own for every write (another provider or a local model) |
| `MEMORY_WRITER_CMD` | Optional: the same for `write_memories.py` run directly |
| `SCRIPTORIUM_TZ` / `MEMORY_WRITER_TZ` | Optional: your time zone (IANA name) for calendar days, for the server / the writer run directly |
