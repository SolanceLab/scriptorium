# Writers

Writers are now called agents. See [Agents](agents.md) for how a key maps to one agent or to several, and how to set them up.

`SCRIPTORIUM_WRITERS` and `MEMORY_ENTRY_WRITERS` still work, as before. `SCRIPTORIUM_WRITERS` also accepts a list of agents now.

Agent and collection names follow the same rules. Use names without a leading dot or dash, trailing dot or space, path separators, control characters, Windows-reserved names, or the characters `: * ? " < > |`. Use the same spelling and case when referring to a name. Names that differ only by case or Unicode normalization are refused. The server derives the agent from the authenticated key, or from the `agent` argument when the key is shared, and each entry tool works within that agent's collections.

See [Entry storage and tools](pen.md) for the seven tools and the transfer command.
