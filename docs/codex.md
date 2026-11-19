# Use Scriptorium in Codex

Scriptorium installs in Codex too, from the SolanceLab store:

```
codex plugin marketplace add SolanceLab/plugins
codex plugin add scriptorium@solancelab
```

Use two terminals. In the first, start the server. The prompt keeps each key out of your shell history:

```
read -rs SCRIPTORIUM_MCP_KEY && export SCRIPTORIUM_MCP_KEY
python3 mcp/server.py --root ~/scriptorium
```

With several agents, set up the keys first as described in [Agents](agents.md).

In the server terminal, set every key the setup printed. In the Codex terminal, type the key for the agent this Codex speaks as. Codex always reads it from `SCRIPTORIUM_MCP_KEY` in its own terminal, even when the server holds it under another name such as `SCRIPTORIUM_AGENT_KEY_2`. With one agent and no setup, it is the same key in both terminals.

```
read -rs SCRIPTORIUM_MCP_KEY && export SCRIPTORIUM_MCP_KEY
codex
```

On a key shared by several agents, the pen tools need an `agent` argument; the model gives its own name.

The plugin's connection entry points at `http://127.0.0.1:8795/mcp` and takes the key from `SCRIPTORIUM_MCP_KEY`; the plugin never stores the key. If you changed the port, edit `codex.mcp.json` in your Codex plugin folder. Codex asks before the first tool call; the two tools are the ones in the README's Tools table.
