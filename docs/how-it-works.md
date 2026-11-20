# How it works, in depth

0. **The journal.** Your AI writes its own entries with the pen ([pen.md](pen.md)). No script writes there: everything below is the assistant, which writes notes.
1. **Notes.** The writer hands one conversation (or one day of a long one) at a time to the model with the instructions in `engine/prompt-public.md`. The model proposes notes: topic, summary, the exact words (excerpt), the source line ids, the date, and three labels: **salience** (how much the moment changed things), **valence** (heavy to light) and **emotions**.
2. **Checked before kept.** Every proposed note is checked against the real lines: the line ids must exist in that conversation, and every piece of the excerpt must appear word for word in one line it cites. A date that is hedged ("likely 7 July"), a weekday that does not match its date, or "last Friday" turned into the wrong Friday is rejected. A note that fails is reported, never kept.
3. **People cards.** `write_people` reads all of one person's notes at once. The model names the notes each fact rests on; the code assembles the exact words and line ids from those notes, so a quote can never be paraphrased. Code, not the model, decides who gets a card.
4. **The roster, nightly.** New messages are grouped by the person who wrote them or is named in them, read per person, and every proposed fact is checked against the real message (the quote word for word, the person named or the writer, no negative or past wording without a hand check). See [roster.md](roster.md).
5. **Provenance.** Every output file records the exact model command, the model that answered, its cost, the writer's version and the fingerprint of the instructions used.

The `memories` field holds notes; it keeps its old name so files written before stay readable.

**Run the writers by hand**, without the plugin:

```
python3 engine/write_notes.py --export conversations.json --out notes --model claude-sonnet-5-5
python3 engine/write_people.py --export conversations.json --memories notes --out people --model claude-sonnet-5-5
```

(`engine/write_memories.py` is the old name of the first and still works; `--memories` keeps its old name and takes the notes folder.)

**Any model.** `--model-cmd "<command>"` (or `SCRIPTORIUM_MODEL_CMD` for the server) runs any command that reads the instructions on its input and prints its answer, so you can use another provider or a local model. The default Claude command has every tool switched off; a command of your own gets only the protections you give it.

**Under development.** Salience, valence and emotions are written on every note today. Using salience and valence to rank recall, letting unasked notes fade (grief faster than joy) and strengthen when used, and marking an older note as replaced by a newer one are under development. Emotions are shown with a note, never used to search for one.

Notes files work as a store for [Antechamber](https://github.com/SolanceLab/antechamber), House of Solance's recall engine, which shows each note with its source lines.

## Updates

Third-party marketplaces do not auto-update by default. To turn it on once: `/plugin` > Marketplaces > `scriptorium` > Enable auto-update. To update by hand: `claude plugin update scriptorium@scriptorium`, then restart. The writers and the server update with `git pull`.

On each new session the plugin makes one request for its public version file (`plugin/.claude-plugin/plugin.json` on GitHub), at most once a day (once an hour while the request fails), and shows one line when a newer version is out. It sends nothing about you. To switch the check off, set `PLUGIN_UPDATE_CHECK=0`.
