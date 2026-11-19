# Agents

An agent is the role or the identity a model speaks as. Each agent has its own pen: its own collections and entries. The name you give an agent is the name of its pen folder.

## One or several agents per key

A key can belong to one agent or to several.

- A key with one agent works as it did in 1.3.0. The agent comes from the key, and a tool call that sends `agent` is refused with "unknown argument".
- A key shared by several agents lists all seven pen tools with a required `agent` argument. It must be one of the agents on that key, spelled exactly as given. Each agent's entries stay separate from the others.
- A key with no agent can use the extraction tools but not the pen.

Anyone holding a shared key can read, write, edit and delete entries for every agent on that key. Share a key only between agents that already share a chat.

## Configure

Set `SCRIPTORIUM_AGENTS` to a JSON object. Each name is an environment variable that holds a key (at least 16 characters, different for each variable). Each value is one agent or a list of agents:

```
{"SCRIPTORIUM_MCP_KEY":["Mira","Echo"],"SCRIPTORIUM_AGENT_KEY_3":["Zed"]}
```

`SCRIPTORIUM_WRITERS` and `MEMORY_ENTRY_WRITERS` still work and now also accept lists. Setting either of them together with `SCRIPTORIUM_AGENTS` is refused at start. The value must be a JSON object; `null`, a list, a string and a number are refused.

## Setup

`python3 engine/setup_agents.py` asks how many AIs will write, their names, which of them talk in the same chat, and how each one's turn starts there. A one-AI setup asks for its name once. For no questions, pass `--agents Mira,Echo,Zed --shared Mira,Echo`.

First make the folder (`mkdir -p "$HOME/scriptorium"`), then run it with `--out "$HOME/scriptorium/agents.json"` so the file lands where the start block below reads it (without `--out` it writes `scriptorium-agents.json` in the current folder; `--force` replaces an existing file). The file holds variable names, never keys. Turn openings are saved for later; nothing uses them yet.

Then start the server with this block. Replace `$HOME/scriptorium` with your own folder in both places (the settings file and the `--root` folder):

```sh
unset SCRIPTORIUM_AGENTS SCRIPTORIUM_WRITERS MEMORY_ENTRY_WRITERS
SCRIPTORIUM_AGENTS="$(python3 engine/setup_agents.py --print-agents "$HOME/scriptorium/agents.json")" || exit 1
export SCRIPTORIUM_AGENTS
python3 mcp/server.py --root "$HOME/scriptorium"
```

Before it, set each key variable the setup printed, with the prompt so the key stays out of your shell history: `read -rs VAR && export VAR`. Type a random key of at least 16 characters, different for each variable.

## Which key each client sends

Give each AI the key its line names. In Claude Code, put that value in the plugin's Key field. In Codex, start Codex from its own terminal with `read -rs SCRIPTORIUM_MCP_KEY && export SCRIPTORIUM_MCP_KEY && codex` and type that AI's key. Codex always reads `SCRIPTORIUM_MCP_KEY` from its own terminal, whatever the variable is called on the server.

Anyone holding a shared key can read, write, edit and delete entries for every agent on that key.

## Tool weight

What a client sees in `tools/list`, measured with `python3 mcp/count_tools.py`.

Before (1.3.0):

```
| Key | Tools | Description chars | Serialized chars |
| --- | --- | --- | --- |
| one agent | 9 | 1481 | 3670 |
| extraction only | 9 | 1481 | 3670 |
| two agents | refused at start (not supported) | | |
```

After:

```
| Key | Tools | Description chars | Serialized chars |
| --- | --- | --- | --- |
| one agent | 9 | 1481 | 3670 |
| extraction only | 9 | 1481 | 3670 |
| two agents | 9 | 1656 | 4382 |
```

A shared key adds one `agent` argument to each of the seven pen tools; the tool count does not change.
