# Scriptorium

Made by House of Solance · free for noncommercial use · credit required

Scriptorium gives your AI a journal of its own, written in its own hand, and an assistant that reads your chats back so nothing that matters slips.

<div align="center">

📜

*Imagine your AI keeps a journal. It writes the moments it chose to remember, revises them as it understands more, and pins the ones it wants to keep close. The journal is its own: no script writes in it, only the AI holding its pen.*

*But most of what happens is talk, thousands of lines a day, and no one can copy all of it down. Someone mentions they moved, a birthday, a new job, once and in passing. A week later it's gone.*

**So the writer has an assistant at its elbow.** *When the day goes quiet, the assistant reads back the talk and drafts what matters: each note in the speaker's own words, with the line it came from and the real date, and a page for every person, holding only what they said themselves.*

*The writer keeps the pen. The assistant copies, it never invents, and it never writes in the journal. Anything it can't point to a line for, it sets aside to be checked.*

</div>

<p align="center"><img src="docs/how-it-works.svg" width="900" alt="At the top, your AI's journal, written in its own hand. Below it, your chats go into The assistant: a model you choose drafts notes, then plain code checks every quote against its line. Out come notes with their line and date, a page per person, and what could not be proved, set aside. Antechamber finds the notes again."></p>

Its pair, [Antechamber](https://github.com/SolanceLab/antechamber), is the librarian who finds your notes and chat lines again.

## Quick start

```bash
git clone https://github.com/SolanceLab/scriptorium && cd scriptorium
mkdir -p "$HOME/scriptorium"
read -rs SCRIPTORIUM_MCP_KEY && export SCRIPTORIUM_MCP_KEY
SCRIPTORIUM_AGENTS='{"SCRIPTORIUM_MCP_KEY":"<name of your AI>"}' python3 mcp/server.py --root "$HOME/scriptorium"
```

The key prompt needs bash or zsh (macOS and most Linux shells). The `read` line asks you to type a random key of at least 16 characters; it stays out of your shell history. The `SCRIPTORIUM_AGENTS` line binds that key to your AI, so it can write in its pen. For several AIs, run `python3 engine/setup_agents.py` ([docs/agents.md](docs/agents.md)).

In a second terminal, add the plugin; it asks for the server address and the key:

```
claude plugin marketplace add SolanceLab/plugins
claude plugin install scriptorium@solancelab
```

Give your AI its pen ([docs/agents.md](docs/agents.md)) and it can start its journal. To let the assistant read your chats too, put your `conversations.json` in `~/scriptorium` and ask your AI to write notes from it. In Codex: [docs/codex.md](docs/codex.md).

Prefer not to keep a computer on? The pen can also run in your own free Cloudflare account, side by side with the local server: [docs/cloud.md](docs/cloud.md).

## What it is

**The journal.** Your AI writes its own entries by hand, in collections it names: a journal by day, reflections, lessons, a summary of who it is, whatever it chooses. Every entry keeps its history and can be pinned; a delete sets it aside instead of erasing it.

*Who can write in it.* Each agent has its own pen. A key opens the pens of the agents it is bound to: one agent, or several that share a chat ([docs/agents.md](docs/agents.md)). Anyone holding a shared key can write in every pen on it. A key bound to no agent can use only the assistant. No script writes into it: the assistant's tools refuse any path inside the pen folder and never load the pen, and the tests check both. What goes in is what your AI chooses to write, even when it quotes a chat.

**The assistant.** It reads your chat history and writes notes: each one checked word for word against the line it came from, and one card for every person who keeps coming up. Every night it can read your new messages and add what people said about themselves to their cards. Notes are offered to your AI and to you; the assistant never writes them into the journal. Your AI decides what to write there itself. It is made for conversations, the people and moments in them, not for coding sessions.

| Word | Who writes it | From |
|---|---|---|
| Journal | your AI, by hand (the pen) | its own choice |
| Notes | the assistant (a script) | your chat history, each note tied to its line |
| Chat history | nobody: it is what was said | your chats |
| People cards | the assistant, with cited lines | your chat history |

## What you need

- Python 3.9 or newer (standard library only).
- For the assistant: a Claude-format chat export (`conversations.json`), or your own store ([docs/your-own-store.md](docs/your-own-store.md)), and a model to write with: Claude Code by default (`claude` on your PATH, signed in, every tool switched off), or any other ([docs/how-it-works.md](docs/how-it-works.md)).

Everything runs on your own computer. Your conversations go only to the model you choose.

## Settings

| Setting | What it is |
|---|---|
| Server address | Where your Scriptorium server answers: `http://127.0.0.1:8795/mcp` unless you changed the port |
| Key | The key bound to your agent in `SCRIPTORIUM_AGENTS` (setup writes it; see [Agents](docs/agents.md)). Even with one agent, bind its key: a key bound to no agent opens only the assistant, not the pen. |

## Tools

**The journal**, your AI's own pen ([docs/pen.md](docs/pen.md)):

| Tool | What it does |
|---|---|
| `pen_collections` | Lists your AI's collections and how many entries each holds. |
| `pen_write` | Writes a new entry into a collection; the first write creates it. |
| `pen_edit` | Revises an entry; the earlier version is kept. |
| `pen_read` | Reads an entry, or any earlier version of it. |
| `pen_list` | Lists a collection's entries, newest first; pinned only, or one day. |
| `pen_history` | Every version of an entry, oldest first. |
| `pen_delete` | Sets an entry aside with its history; nothing is erased. |

A skill, pen-writing, tells your AI how to write entries it can find again later.

**The assistant**, which reads your chats:

| Tool | What it does |
|---|---|
| `write_notes` | Writes notes from a chat export, one file per conversation. Call it again to continue a long export. Its old name, `write_memories`, still works. |
| `write_people` | Gathers what one person said across your notes into one card, with source lines. "Jean-Luc" and "Jean Luc" are one person. |

The nightly roster, the People page and running everything by hand: [docs/roster.md](docs/roster.md).

## People page

Your people, one card each, in your browser. What the nightly run found waits under Suggestions: a value for a card's field, or a fact it could not prove by itself and set aside for you to check by hand, with the reason and the words it came from. Accept puts it on the card; Dismiss drops it; either way it does not come back. Start it with `make people-page CARDS=people OUT=found` (it answers only on this computer).

<p align="center"><img src="docs/people-page.png" width="800" alt="The People page. Under Suggestions, for a person named Alma: a place, Lisbon, from the words 'Alma moved to Lisbon'; and a fact set aside to check by hand, 'Used to live in Porto', with the reason (the sentence is in the past) and the words it came from. Each has an Accept and a Dismiss button."></p>

## Limits

- A person gets a card after at least 2 sessions and 3 notes.
- `write_notes` writes at most 50 conversations per call; the nightly roster reads at most 60 messages per person per run, and the rest wait.
- About 2 to 5 US cents per conversation and 8 to 13 cents per person card, with Claude Sonnet.
- The server answers only on this computer, inside one folder you choose.

Every limit, with its number: [docs/limits.md](docs/limits.md).

## How it works

The model proposes; the code checks. A note is kept only if every word of its quote appears in the line it cites and its date holds up. Facts about a person are kept only when the person said them, or is named in the line. Anything that fails is reported or set aside, never kept quietly. More: [docs/how-it-works.md](docs/how-it-works.md).

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
