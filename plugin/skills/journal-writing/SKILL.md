---
name: journal-writing
description: >-
  Use when the person says "journal this", "journal it" or "write in your journal", and when
  writing, pinning or revising an entry in one of your own journals (hand-written records such as a
  journal, reflections, lessons or a summary), so it can be found again later. Not for extracting
  notes from chat exports.
---

# Writing in your journal with Scriptorium

A journal is what you write in your own words, as your own record. You can name a journal for any purpose: a journal, reflections, lessons, a summary. "Journal this" means `journal_write`.

- Write the entry in your own words. Never paste a chat transcript.
- Keep one moment or idea per entry. Choose a journal name that says what it holds, such as `project-decisions` or `field-notes`. Use `journals` to find an existing journal and the fields it declares.
- Some journals declare fields (for example emotions, keynotes or a decision). Pass them in `fields` on `journal_write`; a field the journal does not declare is refused, and the answer names the ones it allows. `journal_edit` merges `fields` into the entry's own, and a null clears an optional field.
- Start the body by saying plainly what happened or what you learned. Use the words you would search for later, including the names of people and places, the concrete event or decision, and the date if it matters.
- Keep any quotation exact and mark it as a quote. Add one line that says why the moment or idea matters.
- If your key is shared with other agents, every journal tool needs an `agent` argument: give your own name, spelled exactly.
- Use `journal_write` for a new entry. Pin only what changes who you are.
- When rewriting a summary entry, read it with `journal_read` and use its version as `based_on` in `journal_edit`. Read the entry again before you build on it: you always see the current wording, and earlier versions are not offered.

## Finding entries with Antechamber

Antechamber is a separate plugin that helps find what you wrote. Plain words beat metaphors when the words in the entry match what you will look for later. Keep one subject per entry and put names and relevant dates in the body itself.
