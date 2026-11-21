# How it works, in depth

0. **The journal.** Your AI writes its own entries with the pen ([pen.md](pen.md)). No script writes there: everything below is the assistant, which writes notes.
1. **Notes.** The writer sends a conversation or a piece of one to the model. The public instructions in `engine/prompt-public.md` ask for a topic, summary, exact excerpt, citations, date, salience, valence, and emotions. They ask the model to cite every line that supports a claim, including the reply that settles it.
2. **Checked before kept.** Each excerpt fragment must appear word for word within one cited message after whitespace normalization. The writer may add a citation for a missing fragment of at least six words if it matches at word boundaries in exactly one message of the same model call and the excerpt follows transcript order. The raw notes include the recovered citations in `source_line_ids` and list them in `added_line_ids`. This verifies the quote's location. The summary's factual support still depends on what the cited messages say. The date check uses the model's original citations and rejects hedged dates, mismatched weekdays, and incorrect relative dates it can establish from those messages.
3. **People cards.** `write_people` reads all of one person's notes at once. The model names the notes each fact rests on; the code assembles the exact words and line ids from those notes, so a quote can never be paraphrased. Code, not the model, decides who gets a card.
4. **The roster, nightly.** New messages are grouped by the person who wrote them or is named in them, read per person, and every proposed fact is checked against the real message (the quote word for word, the person named or the writer, no negative or past wording without a hand check). See [roster.md](roster.md).
5. **Provenance.** Every output file records how the model was called, the model that answered, its cost when known, the writer's version and the fingerprint of the instructions used.

The `memories` field holds notes; it keeps its old name so files written before stay readable.

**Run the writers by hand**, without the plugin:

```
python3 engine/write_notes.py --export conversations.json --out notes --model claude-sonnet-5-5
python3 engine/write_people.py --export conversations.json --memories notes --out people --model claude-sonnet-5-5
```

(`engine/write_memories.py` is the old name of the first and still works; `--memories` keeps its old name and takes the notes folder.)

**Speaker names.** Set `--user-name` and `--agent-name` or their environment defaults, `SCRIPTORIUM_USER_NAME` and `SCRIPTORIUM_AGENT_NAME`. The public prompt uses explicit speaker names in messages first, then the configured names or “the user” and “the assistant”. The CLI selects the private built-in prompt if installed and the public prompt otherwise. MCP always selects the public prompt and accepts names through the server environment only. A custom prompt path uses the file as written. Configured names in built-in instructions go to the selected model. Writer metadata stores a hash of the rendered instructions.

**API billing.** The notes writer uses your Claude Code login by default. Add `--api` to send conversations directly to the Anthropic Messages API and bill the account tied to `ANTHROPIC_API_KEY`. Set that environment variable before running the command. API mode defaults to `claude-sonnet-5-5` and accepts another API model ID through `--model`. Use `--api` on its own without `--model-cmd` or `MEMORY_WRITER_CMD`.

**Any model.** `--model-cmd "<command>"` (or `SCRIPTORIUM_MODEL_CMD` for the server) runs any command that reads the instructions on its input and prints its answer, so you can use another provider or a local model. The default Claude command has every tool switched off; a command of your own gets only the protections you give it.

**Under development.** Salience, valence and emotions are written on every note today. Using salience and valence to rank recall, letting unasked notes fade (grief faster than joy) and strengthen when used, and marking an older note as replaced by a newer one are under development. Emotions are shown with a note, never used to search for one.

Notes files work as a store for [Antechamber](https://github.com/SolanceLab/antechamber), House of Solance's recall engine, which shows each note with its source lines.

## Updates

Third-party marketplaces do not auto-update by default. To turn it on once: `/plugin` > Marketplaces > `scriptorium` > Enable auto-update. To update by hand: `claude plugin update scriptorium@scriptorium`, then restart. The writers and the server update with `git pull`.

On each new session the plugin makes one request for its public version file (`plugin/.claude-plugin/plugin.json` on GitHub), at most once a day (once an hour while the request fails), and shows one line when a newer version is out. It sends nothing about you. To switch the check off, set `PLUGIN_UPDATE_CHECK=0`.
