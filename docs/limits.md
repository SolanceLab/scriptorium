# Limits, in full

- The server answers only on 127.0.0.1, refuses to start without a key of at least 16 characters, and is given paths only inside its one folder (not your home folder); linked files inside that folder are not followed when reading notes. It trusts that folder: another program that can already write there could redirect a write, so keep it a folder only you use.
- Requests must be JSON, from a local address, at most 1 MB; exports are at most 200 MB.
- One write runs at a time; other requests are still answered. `write_notes` (or its old name, `write_memories`) writes at most 50 conversations per call (default 10) and starts at most 100 model pieces per call (the first conversation of a call always completes); call it again to continue where it stopped. A conversation that would need more than 200 pieces is refused before any model call.
- A conversation is split into pieces: one per calendar day it runs over, and a day longer than about 100,000 tokens is cut again. Each piece is one model call with its own date. Days are read from each message's timestamp as the export writes it (Claude exports use UTC) unless you give your time zone (`--timezone Asia/Jakarta`, the tool's `timezone`, or `SCRIPTORIUM_TZ` for the server); then days and "yesterday" follow your clock. A single message longer than one model call can read fails and is reported.
- Each notes model call may take up to 10 minutes; a failed call is retried once, and a date error gets one corrective retry. A person-card call may take up to 15 minutes and is retried once.
- A note's summary is at most 1,500 characters; it keeps at most 10 emotions and 10 context tags, each at most 60 characters.
- Relative dates ("last Friday") are read off a calendar of the 14 days before and 7 days after each conversation.
- A person gets a card only when they appear in at least 2 sessions and at least 3 notes. A session is one conversation on one day: two chats on the same day count as two, and so do two days of one long chat.
- The nightly roster reads at most 60 messages per person in one run when a cap is set (always in `run_roster`); the rest wait for the next run.
- Cost: about 2 to 5 US cents per conversation for notes and about 8 to 13 cents per person-card pass, measured with Claude Sonnet.
