# Writers

Each authenticated key has at most one writer. Set `SCRIPTORIUM_WRITERS` to a JSON object mapping environment variable names containing keys to writer names, for example `{"FIRST_KEY":"first","SECOND_KEY":"second"}`. Set each referenced variable to a distinct secret of at least 16 characters. The existing `SCRIPTORIUM_MCP_KEY` remains required and can also be referenced; an authenticated key without a writer can use extraction tools but cannot use journal tools. Never put real keys in configuration examples or tool arguments.

Writer names cannot start with a dot or dash, contain path separators or control characters. The server derives the writer from the authenticated key; journal tools do not accept a writer argument.
