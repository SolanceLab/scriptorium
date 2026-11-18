# Scriptorium

Made by House of Solance · free for noncommercial use · credit required

Scriptorium turns your chats into memories you can check, and keeps a page for every person in them.

<div align="center">

📜

*Imagine your AI keeps a journal. Some of it, it writes by hand: the moments it chose to remember. But most of what happens is talk, thousands of lines a day, and no one can copy all of it down.*

*So things slip. Someone mentions they moved, a birthday, a new job, once and in passing. A week later it's gone.*

**Scriptorium is the assistant at the writer's elbow.** *When the day goes quiet, it reads back the talk and drafts what matters: each memory in the speaker's own words, with the line it came from and the real date, and a page for every person, holding only what they said themselves.*

*The writer keeps the pen. The assistant copies, it never invents. Anything it can't point to a line for, it sets aside to be checked.*

</div>

<p align="center"><img src="docs/how-it-works.svg" width="900" alt="Your chats go in on the left. Inside Scriptorium a model you choose drafts from them, then plain code checks every quote against its line, and three things come out: memories, each with the speaker's exact words, its source line and the real date; a page for every person, with only what they said themselves; and a tray of anything it could not prove, set aside for you to check. Below, its pair Antechamber makes all of it findable: your AI asks where Mira lives now and gets back the one right memory, with its line and date. Without them, you would search thousands of lines yourself."></p>

Its pair, [Antechamber](https://github.com/SolanceLab/antechamber), is the librarian who finds it all again.

## Quick start

```
git clone https://github.com/SolanceLab/scriptorium && cd scriptorium
SCRIPTORIUM_MCP_KEY=<a long random key> python3 mcp/server.py --root ~/scriptorium
```

In a second terminal, add the plugin; it asks for the server address and the key:

```
claude plugin marketplace add SolanceLab/plugins
claude plugin install scriptorium@solancelab
```

Put your `conversations.json` in `~/scriptorium`, then ask your AI to write memories from it. In Codex: [docs/codex.md](docs/codex.md).

## What it is

Scriptorium writes the record your AI can trust: memories from your chat history, each one checked word for word against the lines it came from, and one card for every person who keeps coming up. Every night it can also read your new messages and add what people said about themselves to their cards. It is made for conversations, the people and moments in them, not for coding sessions.

## What you need

- Python 3.9 or newer (standard library only).
- A Claude-format chat export (`conversations.json`), or your own store ([docs/your-own-store.md](docs/your-own-store.md)).
- A model to write with: Claude Code by default (`claude` on your PATH, signed in, every tool switched off), or any other ([docs/how-it-works.md](docs/how-it-works.md)).

Everything runs on your own computer. Your conversations go only to the model you choose.

## Settings

| Setting | What it is |
|---|---|
| Server address | Where your Scriptorium server answers: `http://127.0.0.1:8795/mcp` unless you changed the port |
| Key | The key you started the server with (`SCRIPTORIUM_MCP_KEY`) |

## Tools

| Tool | What it does |
|---|---|
| `write_memories` | Writes memories from a chat export, one file per conversation. Call it again to continue a long export. |
| `write_people` | Gathers one person's memories into one card, with source lines. "Jean-Luc" and "Jean Luc" are one person. |

The nightly roster, the People page and running everything by hand: [docs/roster.md](docs/roster.md).

## People page

Your people, one card each, in your browser. What the nightly run found waits under Suggestions: a value for a card's field, or a fact it could not prove by itself and set aside for you to check by hand, with the reason and the words it came from. Accept puts it on the card; Dismiss drops it; either way it does not come back. Start it with `make people-page CARDS=people OUT=found` (it answers only on this computer).

<p align="center"><img src="docs/people-page.png" width="800" alt="The People page. Under Suggestions, for a person named Alma: a place, Lisbon, from the words 'Alma moved to Lisbon'; and a fact set aside to check by hand, 'Used to live in Porto', with the reason (the sentence is in the past) and the words it came from. Each has an Accept and a Dismiss button."></p>

## Limits

- A person gets a card after at least 2 sessions and 3 memories.
- `write_memories` writes at most 50 conversations per call; the nightly roster reads at most 60 messages per person per run, and the rest wait.
- About 2 to 5 US cents per conversation and 8 to 13 cents per person card, with Claude Sonnet.
- The server answers only on this computer, inside one folder you choose.

Every limit, with its number: [docs/limits.md](docs/limits.md).

## How it works

The model proposes; the code checks. A memory is kept only if every word of its quote appears in the line it cites and its date holds up. Facts about a person are kept only when the person said them, or is named in the line. Anything that fails is reported or set aside, never kept quietly. More: [docs/how-it-works.md](docs/how-it-works.md).

## The pen

Scriptorium also has a [pen](docs/pen.md). Your AI can write its own entries in named collections, revise them with version history, and pin the entries it wants to keep close. The pen keeps those entries separate from chat extraction.

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
