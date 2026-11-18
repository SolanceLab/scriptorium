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

Set `SCRIPTORIUM_PEN_STORE` to an importable `module:attr` naming an object, class, or zero-argument factory. It must provide `append(writer, entry) -> id`, `edit(writer, id, text)`, `read(writer, id)`, `day(writer, date)`, and `recent(writer, n)`; the last returns newest first. Entries passed to append contain `body` and optionally `title`; reads return dictionaries with at least `id`, `time`, `writer`, and `body`. The store must enforce writer isolation itself. Without this setting the local-files store is used. Configure keys and writers as in [writers.md](writers.md).
