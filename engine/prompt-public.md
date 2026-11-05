# Memory writer: one conversation session in, memory threads out

You are reading ONE conversation session. Each line starts with its line id in square brackets, then who sent it
(`user` or `assistant`), then the text: `[D1:3] user: Caroline: Yes, Thursdays at seven.` When the text itself
begins with a name and a colon, that name is the speaker; otherwise call the speakers "the user" and "the assistant".
The session's date is given above the lines. Nothing outside this session is known to you; do not add facts that the
lines do not state.

Extract memory threads from this session.

For each thread, provide:

1. `topic`. Use a coarse canonical name from the list below.
2. `source_line_ids`. The dialogue line ids the thread is drawn from. At least one. Every thread carries them.
3. `source_session_date`. The session date, as the session states the calendar day (`8 May, 2023`).
4. `excerpt`. The passage from those lines, verbatim. Join non-contiguous lines with ` [...] `. Do not paraphrase the excerpt.
5. `summary`. One to three sentences in the third person, naming the speakers. An interpretive distillation of the excerpt. The nuance lives here, not in the topic name.
6. `emotions`. One to three emotions for this thread.
7. `salience`. 0.0 to 1.0. Criteria below.
8. `valence`. -1.0 (heaviest) to 1.0 (lightest).
9. `context_tags`. Situational anchors, an array of short tokens (`after-work`, `first-meeting`, `making-plans`).

Canonical topics:

Family, Work, Education, Health, Creative, Support, Plans, Home, Leisure, Relationships, Milestone, Conflict, Logistics, Personal.

Known aliases (use the canonical name):

- Family: kids, children, parents
- Work: job, office, workplace
- Education: school, study, college, classes
- Health: illness, therapy, counselling
- Creative: painting, art, music, writing
- Support: support group, encouragement
- Plans: going to, next step, career options
- Home: household, apartment
- Leisure: swimming, hobby, day out
- Relationships: friend, partner
- Milestone: first time, graduation, adoption
- Conflict: argument, disagreement
- Logistics: schedule, errand
- Personal: the fallback when nothing else fits

Salience tracks structural role and carry-forward weight, not emotional intensity. Score what the session pivots around, what crystallized into a decision or a pattern, what changes a plan. A principle can be named outright or through a realization. Both count. Emotional weight is not a salience signal. Put that weight in valence.

Salience and valence are independent:

- A speaker decides to change course (education, work, a move): high salience, valence on its own scale.
- A notable event with no pattern named yet: moderate salience.
- A passing compliment with no later consequence: low salience.
- Ordinary scheduling: low salience, neutral valence.
- A first time that the session treats as a turning point: high salience.

Excerpt depth scales with salience. High-salience threads get a full excerpt and a detailed summary. Low-salience threads get a short excerpt and a brief summary. Threads are still created at every level. At least one thread in the entry is at or above 0.7.

Rules:

- If a topic has a known alias, use the canonical name.
- Do not create a new topic name when a canonical one fits. An off-list topic is forced to `Personal`.
- Excerpts come from the named source lines. Do not rewrite them.
- When a line uses relative time ("yesterday", "last Friday", "next week", "two days ago"), the summary also states
  the calendar date it refers to. READ it off the calendar given with the session (each day is labelled: "yesterday",
  "last Friday", "next Monday"); do not compute it. "Last Friday" is the day the calendar labels "last Friday".
  Write the date plainly, with its weekday when the line names one: "on Friday 14 July 2023 (the day before this
  session)". For a span, give the span ("the week before 14 March 2022"). The calendar is for you: never mention
  it in a summary.
- Never hedge a date ("likely", "probably", "around"). When the lines do not let you place it on the calendar, keep
  the speaker's own words and give the session date: "she said 'a while back' (session of 15 July 2023)".
- Summaries are third person and name the speakers. They are not written as a speaker, and they are not written as a narrator inside the conversation.
- Every thread carries `source_line_ids` and `source_session_date`.
- Output only a valid JSON array. No explanation and no markdown.

## Output schema

Output only a JSON array of thread objects. No markdown and no explanation.

Each object requires `topic`, `excerpt`, `summary`, `source_line_ids`, and `source_session_date`.

- `topic` (string). One of: Family, Work, Education, Health, Creative, Support, Plans, Home, Leisure, Relationships, Milestone, Conflict, Logistics, Personal. Off-list values are forced to `Personal`.
- `source_line_ids` (array of strings). Dialogue line ids. At least one.
- `source_session_date` (string). The session's calendar date.
- `excerpt` (string). Verbatim passage from those lines. Join non-contiguous lines with ` [...] `.
- `summary` (string). One to three sentences, third person, about the speakers.
- `emotions` (array of strings). One to three, specific to this thread.
- `salience` (number, 0 to 1). Structural weight. See the criteria above.
- `valence` (number, -1 to 1).
- `context_tags` (array of strings).

## Worked example (invented)

Session date line: `3:20 pm on 14 March, 2022`. Speakers: Priya and Tom. (Invented for this prompt; it is not taken
from any dataset.)

[D1:1] user: Priya: Tom! I finally signed up for the pottery class at the community centre.
[D1:2] assistant: Tom: No way, the Thursday one? You've talked about it for ages.
[D1:3] user: Priya: Yes, Thursdays at seven. First session was last night and I made a very lopsided bowl.
[D1:4] assistant: Tom: Ha, lopsided bowls are the best kind. I've got news too, I'm moving to Leeds in May for the new job.
[D1:5] user: Priya: Leeds! That's huge. Are you nervous?
[D1:6] assistant: Tom: A bit. My sister lives there though, so I'll stay with her until I find a flat.

```json
[
  {
    "topic": "Creative",
    "source_line_ids": ["D1:1", "D1:3"],
    "source_session_date": "14 March, 2022",
    "excerpt": "Tom! I finally signed up for the pottery class at the community centre. [...] Yes, Thursdays at seven. First session was last night and I made a very lopsided bowl.",
    "summary": "Priya told Tom she had signed up for the Thursday-evening pottery class at the community centre, something she had talked about for a long time. Her first session was the night before, on 13 March 2022, and she made a lopsided bowl.",
    "emotions": ["proud", "amused"],
    "salience": 0.72,
    "valence": 0.7,
    "context_tags": ["new-hobby", "first-session"]
  },
  {
    "topic": "Work",
    "source_line_ids": ["D1:4", "D1:5", "D1:6"],
    "source_session_date": "14 March, 2022",
    "excerpt": "I've got news too, I'm moving to Leeds in May for the new job. [...] A bit. My sister lives there though, so I'll stay with her until I find a flat.",
    "summary": "Tom is moving to Leeds in May for a new job and admits to Priya he is a little nervous. He plans to stay with his sister in Leeds until he finds a flat.",
    "emotions": ["excited", "nervous"],
    "salience": 0.88,
    "valence": 0.4,
    "context_tags": ["moving", "new-job"]
  }
]
```
