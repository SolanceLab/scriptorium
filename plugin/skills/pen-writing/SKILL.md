---
name: pen-writing
description: >-
  Use when writing, pinning or revising an entry in one of your own collections in the
  Scriptorium pen (hand-written records such as a journal, reflections, lessons or a summary),
  so it can be found again later. Not for extracting notes from chat exports.
---

# Writing in Scriptorium

- Write the entry by hand in your own words. Never paste a chat transcript.
- Keep one moment or idea per entry. Choose a collection name that says what it holds, such as `project-decisions` or `field-notes`. Use `pen_collections` to find an existing collection.
- Start the body by saying plainly what happened or what you learned. Use the words you would search for later, including the names of people and places, the concrete event or decision, and the date if it matters.
- Keep any quotation exact and mark it as a quote. Add one line that says why the moment or idea matters.
- If your key is shared with other agents, every pen tool needs an `agent` argument: give your own name, spelled exactly.
- Use `pen_write` for a new entry. Pin only what changes who you are.
- When rewriting a summary entry, read it with `pen_read` and use its version as `based_on` in `pen_edit`. Its earlier versions stay readable.

## Finding entries with Antechamber

Antechamber is a separate plugin that helps find what you wrote. Plain words beat metaphors when the words in the entry match what you will look for later. Keep one subject per entry and put names and relevant dates in the body itself.
