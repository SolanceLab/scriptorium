# Writers

Each authenticated key has at most one writer. Set `SCRIPTORIUM_WRITERS` to a JSON object mapping environment variable names containing keys to writer names, for example `{"FIRST_KEY":"first","SECOND_KEY":"second"}`. Set each referenced variable to a distinct secret of at least 16 characters. The existing `SCRIPTORIUM_MCP_KEY` remains required and can also be referenced; an authenticated key without a writer can use extraction tools but cannot use pen tools. Never put real keys in configuration examples or tool arguments.

Writer and collection names follow the same rules. Use names without a leading dot or dash, trailing dot or space, path separators, control characters, Windows-reserved names, or the characters `: * ? " < > |`. Use the same spelling and case when referring to a name. Collection names that differ only by case or Unicode normalization are refused. The server derives the writer from the authenticated key. Each pen tool works within that writer's collections and accepts only its documented arguments.

See [The pen](pen.md) for the seven tools and the transfer command.
