# Use Scriptorium in Codex

Scriptorium installs in Codex too, from the SolanceLab store:

```
codex plugin marketplace add SolanceLab/plugins
codex plugin add scriptorium@solancelab
```

Use two terminals, and set the key in both. In the first, start the server (the prompt keeps the key out of your shell history):

```
read -rs SCRIPTORIUM_MCP_KEY && export SCRIPTORIUM_MCP_KEY
python3 mcp/server.py --root ~/scriptorium
```

In the second, enter the same key the same way, then start Codex from that terminal so it can read the key:

```
read -rs SCRIPTORIUM_MCP_KEY && export SCRIPTORIUM_MCP_KEY
codex
```

The plugin's connection entry points at `http://127.0.0.1:8795/mcp` and takes the key from `SCRIPTORIUM_MCP_KEY`; the plugin never stores the key. If you changed the port, edit `codex.mcp.json` in your Codex plugin folder. Codex asks before the first tool call; the two tools are the ones in the README's Tools table.
