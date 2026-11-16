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

## Optional account ids (found in chats)

- A message may carry `sender_id`: the author's account id on the chat platform. It decides before the sender's name; an id no card knows ties the message to no one (a shown name can be anyone's).
- A card may carry `ids`: account ids that belong to that person alone. A message with one of them is tied to that card, whatever name it shows (except the export's own speaker, `owner` or `user`, which is never a card); if two cards list the same id, it ties to neither.
- A card may carry `shared_ids`: ids of accounts that speak for several people (one account voicing several characters, or a relay). A message from such an account is tied by its shown name, among the cards that list that id only.
- Ids are used for matching only and are never sent to the model.

## Plug in your own store (optional, nightly)

`nightly.py` reads a chat export and writes a results folder. To read from your own store and save to your own place, name a source and a sink as `module:attr` (an object, or a class or function that builds one with no arguments); the module must be importable from where you run it.

```
python3 engine/nightly.py --out state --source my_store:source --sink my_store:sink
```

- **source**: `read(cursor)` returns the messages newer than the cursor, oldest first, each `{id, sender, said_at, text}` and optionally `sender_id`, `room`, `thread`. The cursor is the dict kept in `state/cursor.json` (`{}` the first time; it holds `last_said_at`, `last_id` and `last_ids`, the ids already read at that time). `cards()` returns the person cards, in the shape `found_in_chats.py` reads.
- **sink**: `write(facts, suggestions)` returns `{stored, skipped, rejected, deferred}`: whole numbers for the first, second and last, and `rejected` a list of `{item, reason}`. It may add `facts_stored` and `suggestions_stored`. Writing the same item again must be safe and is counted as `skipped`, so a run that crashed half way can be repeated without losing or doubling anything.
- **the runner** (`engine/roster.py`, `run_roster(source, sink, state_dir, model_cmd=...)`, also callable from your own code): messages newer than `settle_minutes` wait for a later run (10 in `run_roster`; `--settle-minutes` in `nightly.py`, 0 by default); at most `per_person_cap` messages per person are read in one run (60 in `run_roster`; `--per-person-cap` in `nightly.py`, no cap by default), the rest wait and the run's `notes` say so; one run at a time.
- **the cursor** moves only when no person failed, every message had a time, the sink answered in the shape above and deferred nothing. An item the engine or the sink rejected does not hold it.
- **state files**, all in `state/`: `cursor.json`; `status.json` (`ok`, `at`, `messages`, `people_seen`, `facts_stored`, `suggestions_stored`, `skipped`, `rejected_by_reason`, `notes`, `error`, and more), written whole each run; `rejected.jsonl`, one line for each rejected item with its reason and where it was turned away (`engine` or `sink`); a lock file.

## Settings and secrets it needs

Named here, never valued.

| Name | What it is |
|---|---|
| `SCRIPTORIUM_MCP_KEY` | The key every request to the server must carry (at least 16 characters) |
| `--root` | The one folder the server may read and write |
| `SCRIPTORIUM_MODEL_CMD` | Optional: a model command of your own for every write (another provider or a local model) |
| `MEMORY_WRITER_CMD` | Optional: the same for `write_memories.py` run directly |
| `SCRIPTORIUM_TZ` / `MEMORY_WRITER_TZ` | Optional: your time zone (IANA name) for calendar days, for the server / the writer run directly |
