# People cards: many sessions' memories in, one card per recurring person out

You are given the memories already written from MANY conversation sessions with the same people, oldest first. Each
memory has a number (M1, M2, ...), its session date and its summary. Existing cards, if any, come first; update them
rather than starting again.

Your job is to join what the sessions say about each person, so that a question like "what desserts does Nate like?"
or "which places has Caroline visited?" finds every piece in one place.

Write items for EVERY person the memories mention, including people known only by a role ("her mom", "the
coach"), and cite every memory about them. Which people get a card is decided afterwards from your citations, so leave
no one out and cite fully.

A card is a list of ITEMS. Each item is one aspect of the person (their family, their work, places they have been, food
they like, a long-running project, their health, their plans), and gathers EVERY fact on that aspect from every
session, each fact with its own date. The first item of every card says who the person is: how they are related to the
other speakers, other names used for them, and their pronouns ONLY if a line states them.

For each item provide:

1. `person`. The person's name as the speakers use it.
2. `aspect`. A few words naming the aspect ("desserts and baking", "who they are").
3. `summary`. The facts, in the third person, each with its date: "Nate likes coconut ice cream (said 3 April 2023)
   and baked a dairy-free chocolate cake for his sister (12 May 2023)." Take dates from the memories as written; never
   compute or hedge a date. At most 1,500 characters.
4. `memories`. The numbers of EVERY memory the facts come from (["M3", "M17"]). The speakers' exact words and the
   line ids are taken from those memories automatically, so cite each memory you used and only those.

Rules:

- Use only what the memories state. Do not add, infer or complete a fact. If two sessions disagree, give both, each
  with its date, and the later one last.
- Keep every fact of an aspect in the same item. A list ("the desserts", "the places") is complete only if every
  session's piece is in it.
- No fact may appear without the memories it came from.
- Output only a valid JSON array of items. No explanation and no markdown.
