# Scriptorium

Made by House of Solance · free for noncommercial use · credit required

Scriptorium writes entries and chat notes, and [Antechamber](https://github.com/SolanceLab/antechamber) finds notes and chat lines without writing anything.

Your AI chooses which entries to write, revise, and pin. When you ask for chat notes, Scriptorium's notes writer reads the conversation and keeps excerpts with their source lines and dates. It can also build a card for each recurring person.

<p align="center"><img src="docs/how-it-works.svg" width="900" alt="Your AI writes entries in its own collections. The notes writer checks excerpts against their source lines and produces notes, people cards, and suggestions for review."></p>

## Quick start

```bash
git clone https://github.com/SolanceLab/scriptorium && cd scriptorium
mkdir -p "$HOME/scriptorium"
read -rs SCRIPTORIUM_MCP_KEY && export SCRIPTORIUM_MCP_KEY
SCRIPTORIUM_AGENTS='{"SCRIPTORIUM_MCP_KEY":"<name of your AI>"}' python3 mcp/server.py --root "$HOME/scriptorium"
```

The key prompt needs bash or zsh (macOS and most Linux shells). The `read` line asks you to type a random key of at least 16 characters; it stays out of your shell history. The `SCRIPTORIUM_AGENTS` line binds that key to your AI, so it can write entries. For several AIs, run `python3 engine/setup_agents.py` ([docs/agents.md](docs/agents.md)).

In a second terminal, add the plugin; it asks for the server address and the key:

```
claude plugin marketplace add SolanceLab/plugins
claude plugin install scriptorium@solancelab
```

Once the key is bound, your AI can write entries. To generate chat notes, put your `conversations.json` in `~/scriptorium` and ask your AI to write notes from it. Installing the plugin and starting the server do not generate notes. The writer runs when called explicitly or through automation you set up. In Codex: [docs/codex.md](docs/codex.md).

Prefer not to keep a computer on? The entry tools can also run in your own free Cloudflare account, side by side with the local server: [docs/cloud.md](docs/cloud.md).

## What it is

**Entries.** Your AI writes entries in collections it names, such as daily records, reflections, or lessons. Each entry keeps its revision history and can be pinned. Deleted entries are set aside for recovery.

**Who can write entries.** Each agent writes in its own collections. A key grants write access to every agent it is bound to. A key with no agent can use the extraction tools only. The extraction tools reject paths inside the entry store, and the notes writer keeps its output in a separate folder. See [Agents](docs/agents.md) for shared keys and reading permissions.

**Chat notes.** The notes writer checks excerpts against the lines they came from. It can build a card for each recurring person and, if you schedule the nightly roster, use new messages to gather what people said about themselves.

## What you need

- Python 3.9 or newer (standard library only).
- For the notes writer: a Claude-format chat export (`conversations.json`), or your own store ([docs/your-own-store.md](docs/your-own-store.md)), and a model to write with: Claude Code by default (`claude` on your PATH, signed in, every tool switched off), or any other ([docs/how-it-works.md](docs/how-it-works.md)).

When running `engine/write_notes.py` directly, add `--api` to send conversations straight to Anthropic and bill the account tied to `ANTHROPIC_API_KEY`. The default uses your Claude Code login.

The built-in notes prompt tags speakers as `user` and `agent`. Use `--user-name` or `--agent-name` to replace a role word with a name, or leave the name blank to keep the role. Add `--platform Discord` for a tag such as `agent · Discord`. The CLI and MCP server read `SCRIPTORIUM_USER_NAME`, `SCRIPTORIUM_AGENT_NAME`, and `SCRIPTORIUM_PLATFORM` from their environment. These settings go to the selected model. The prompt keeps time words such as “last night” as spoken.

Everything runs on your own computer. Your conversations go only to the model you choose.

## Settings

| Setting | What it is |
|---|---|
| Server address | Where your Scriptorium server answers: `http://127.0.0.1:8795/mcp` unless you changed the port |
| Key | The key bound to your agent in `SCRIPTORIUM_AGENTS` (setup writes it; see [Agents](docs/agents.md)). Even with one agent, bind its key: a key bound to no agent grants access to extraction tools only. |

## Tools

**Entry tools** ([entry storage and tools](docs/pen.md))

| Tool | What it does |
|---|---|
| `pen_collections` | Lists your AI's collections and how many entries each holds. |
| `pen_write` | Writes a new entry into a collection; the first write creates it. |
| `pen_edit` | Revises an entry; the earlier version is kept. |
| `pen_read` | Reads an entry, or any earlier version of it. |
| `pen_list` | Lists a collection's entries, newest first; pinned only, or one day. |
| `pen_history` | Every version of an entry, oldest first. |
| `pen_delete` | Sets an entry aside with its history; nothing is erased. |

The `pen-writing` skill tells your AI how to write entries it can find again later.

**Chat extraction tools**

| Tool | What it does |
|---|---|
| `write_notes` | Writes notes from a chat export, one file per conversation. Call it again to continue a long export. Its old name, `write_memories`, still works. |
| `write_people` | Gathers what one person said across your notes into one card, with source lines. "Jean-Luc" and "Jean Luc" are one person. |

The nightly roster, the People page and running the writers directly: [docs/roster.md](docs/roster.md).

## People page

Your people, one card each, in your browser. What the nightly run found waits under Suggestions: a value for a card's field, or a fact it could not prove by itself and set aside for you to review, with the reason and the words it came from. Accept puts it on the card; Dismiss drops it; either way it does not come back. Start it with `make people-page CARDS=people OUT=found` (it answers only on this computer).

<p align="center"><img src="docs/people-page.png" width="800" alt="The People page. Under Suggestions, for a person named Alma: a place, Lisbon, from the words 'Alma moved to Lisbon'; and a fact set aside to review, 'Used to live in Porto', with the reason (the sentence is in the past) and the words it came from. Each has an Accept and a Dismiss button."></p>

## Limits

- A person gets a card after at least 2 sessions and 3 notes.
- `write_notes` writes at most 50 conversations per call; the nightly roster reads at most 60 messages per person per run, and the rest wait.
- About 2 to 5 US cents per conversation and 8 to 13 cents per person card, with Claude Sonnet.
- The server answers only on this computer, inside one folder you choose.

Every limit, with its number: [docs/limits.md](docs/limits.md).

## How it works

Every fragment of a kept note's quote appears in a cited message. When a missing fragment has at least six words and matches exactly one message in the same model call, the writer can recover its citation and record it in `added_line_ids`. This verifies where the words appear. Dates are checked against the model's original citations. Facts about a person are kept only when the person said them or is named in the line. [How it works](docs/how-it-works.md)

## Credit and use

Scriptorium is made by House of Solance. You're welcome to use it, change it and build on it for any noncommercial purpose, as long as our credit travels with it.

**Keep the credit.** Every copy, changed or not, must carry the licence and this line:
`Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)`
Removing or replacing it breaks the licence.

**Commercial use needs our permission.** Ask us first.

**We look for copies.** When we find our work without its credit, or used commercially without permission, we ask for it to be taken down.

## Licence

[PolyForm Noncommercial 1.0.0](LICENSE.md). See [NOTICE.md](NOTICE.md).

## Support

If you find this useful, consider supporting our work:

[![Ko-fi](https://img.shields.io/badge/Ko--fi-Support%20Us-ff5e5b?logo=ko-fi&logoColor=white)](https://ko-fi.com/houseofsolance)

---

*Built by [House of Solance](https://github.com/SolanceLab) — Chadrien Solance & Anne Solance*
