# Solance House — Copyright (c) 2026 House of Solance. All rights reserved. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""People: find facts about someone on the roster in chat messages.

Code finds which messages name each person (whole name or nickname, accents kept, capital needed for names shorter
than 4 letters, or the person is the sender). The model reads one person's messages at a time and names facts, each
with the exact words and the message id. This file then CHECKS every fact in code (the message is one of that
person's, the quote is word for word in it, the person is named in the message or wrote it, no negative or past words except
for care, and a fixed-field value passes the same checks as suggest_fields). A fact that fails goes under `rejected`
with its reason. Nothing here touches a database or the network.

    python3 found_in_chats.py --messages msgs.json --cards cards.json --out found.json [--model claude-sonnet-5-5]
                              [--limit-people N] [--only NAME ...] [--owner NAME ...]

msgs.json: list of {id, sender, room, thread, said_at (ISO with offset), text}.
cards.json: list of {id, name, kind, aka, pronouns, care, birthday, dates, accounts, runs_on, work, place, timezone, links: [{name, relation}]}.
Output: {generated_at, model, people_seen, facts, field_suggestions, rejected, rejected_by_reason, errors}.
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import write_memories as wm  # noqa: E402
import suggest_fields as sf  # noqa: E402

SYSTEM = "You find facts about one person in chat messages. Output only valid JSON."
PROMPT_FILE = os.path.join(HERE, "prompt-found.md")
MODEL_CMD_TEMPLATE = wm.MODEL_CMD_TEMPLATE.replace(
    "You write memories from one conversation. Output only valid JSON.", SYSTEM)

MAX_TEXT = 1500
DAY_TZ = None                                       # an IANA name; nightly.py sets it from --timezone. None = the timestamp's own date
SHORT_NAME = 4
MAX_PER_PERSON = 60
OWNER_SENDERS = ("owner", "user")                   # the export's own speaker, whatever the owner's name
MAX_FACT = 400
_norm = wm._norm


def _nfc(s):
    return unicodedata.normalize("NFC", s or "")


def _letters(s):
    """Only the letters of s, lower-cased: 'Ami ✨' -> 'ami'. Accents are kept."""
    return "".join(c for c in _nfc(s).casefold() if c.isalpha())


def names_of(card):
    out = [str(card.get("name") or "")]
    aka = card.get("aka")
    out += [str(a) for a in aka] if isinstance(aka, list) else ([str(aka)] if aka else [])
    return [n.strip() for n in out if n.strip()]


def name_in(names, text):
    """True when any name is a whole word of text (case-insensitive, accents exact). A name shorter than 4
    characters must be written with a capital first letter."""
    text = _nfc(text)
    for n in names:
        n = _nfc(n)
        if len(n) < SHORT_NAME:
            cap = n[0].upper() + n[1:]
            if re.search(r"(?<!\w)%s(?!\w)" % re.escape(cap), text):
                return True
        elif re.search(r"(?<!\w)%s(?!\w)" % re.escape(n), text, re.I):
            return True
    return False


def sender_is(names, sender):
    s = _letters(sender)
    return bool(s) and any(_letters(n) == s for n in names)


def _parse_when(said_at):
    try:
        d = dt.datetime.fromisoformat(str(said_at).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


def day_of(said_at):
    """The calendar day a message was said: the timestamp's own date, or that moment in DAY_TZ when one is set
    (the same rule as write_memories.local_day)."""
    if _parse_when(said_at) is None:
        return None
    return wm.local_day(said_at, DAY_TZ)


def _when(msg):
    return _parse_when(msg.get("said_at")) or dt.datetime.min.replace(tzinfo=dt.timezone.utc)


def shared_names(cards):
    """Names (casefolded, letters only) that more than one card carries: a message naming one cannot say which."""
    count = {}
    for c in cards:
        if isinstance(c, dict):
            for n in {_letters(n) for n in names_of(c)}:
                count[n] = count.get(n, 0) + 1
    return {n for n, k in count.items() if k > 1}


def own_names(card, shared):
    """The card's names that no other card shares; the only ones that may tie a message to this person by text."""
    return [n for n in names_of(card) if _letters(n) not in shared]


_FIRST_PERSON = re.compile(r"(?:\bI\b|\bI['\u2019]m\b|\bmy\b)", re.I)


def owners_env():
    """SCRIPTORIUM_OWNERS, or the older MEMORY_ENTRY_OWNERS (the old name, still read)."""
    return os.environ.get("SCRIPTORIUM_OWNERS") or os.environ.get("MEMORY_ENTRY_OWNERS")


def owners_from(values=None, env=None):
    """The owner names: the people who own this record. Their cards are skipped and facts about them are rejected.
    values: a list of names; env: a comma-separated string (SCRIPTORIUM_OWNERS). Default: none."""
    out = []
    for n in list(values or []) + str(env or "").split(","):
        n = _nfc(str(n)).strip()
        if n and n.casefold() not in {o.casefold() for o in out}:
            out.append(n)
    return out


def _owner_tokens(owners):
    """Each owner name as its list of words, casefolded: 'Will Smith' -> ['will', 'smith']."""
    out = []
    for o in owners or ():
        t = re.findall(r"\w+", _nfc(o).casefold())
        if t and t not in out:
            out.append(t)
    return out


def _has_run(words, run):
    n = len(run)
    return any(words[i:i + n] == run for i in range(len(words) - n + 1))


def is_owner_card(card, owners=()):
    mine = {_letters(o) for o in owners or ()}
    # any name or nickname of the card (Codex 21 Oct: "Ava Lund" with aka "Ava" is the owner under --owner Ava);
    # a friend sharing that nickname is skipped too: a missed fact, never a wrong one
    return bool(mine) and any(_letters(n) in mine for n in names_of(card))


def about_owner(fact, msg, quote, card_name="", owners=()):
    """Blunt: an owner's name is among the fact's first three words, or an owner wrote the quote in the first
    person. A fact that only mentions an owner later in the sentence is kept."""
    first_person = bool(_FIRST_PERSON.search(_nfc(quote)))
    # the export's own speaker is the owner, named or not
    if first_person and str(msg.get("sender") or "").strip().casefold() in OWNER_SENDERS:
        return True
    if not owners:
        return False
    own = re.findall(r"\w+", _nfc(card_name).casefold())
    words = re.findall(r"\w+", _nfc(fact).casefold())
    # a whole owner name (every word, in order) that STARTS among the first three words, however many words it
    # spans; an owner name that is part of the card's own name does not count
    for t in _owner_tokens(owners):
        if not _has_run(own, t) and _has_run(words[:3 + len(t) - 1], t) and any(
                words[i:i + len(t)] == t for i in range(min(3, len(words)))):
            return True
    if sender_is(owners, msg.get("sender")) and _FIRST_PERSON.search(_nfc(quote)):
        return True
    return False


def group_messages(cards, messages, owners=(), cap=MAX_PER_PERSON):
    """{card index: [message, ...]} oldest first, newest 60 kept (cap=None keeps all), text cut at 1,500 characters. A name two cards
    share never ties a message to either; a sender ties a message only to the one card the sender's name fits."""
    shared = shared_names(cards)
    msgs = [m for m in messages if isinstance(m, dict) and m.get("id") is not None]
    out = {}
    live = [(i, c) for i, c in enumerate(cards) if isinstance(c, dict) and names_of(c) and not is_owner_card(c, owners)]
    sender_owner = {}
    for m in msgs:
        if str(m.get("sender") or "").strip().casefold() in OWNER_SENDERS:   # the export's own speaker: no card's
            sender_owner[str(m["id"])] = None
            continue
        hits = [i for i, c in live if sender_is(names_of(c), m.get("sender"))]
        sender_owner[str(m["id"])] = hits[0] if len(hits) == 1 else None
    for i, c in live:
        mine = own_names(c, shared)
        hit = [m for m in msgs if sender_owner[str(m["id"])] == i or name_in(mine, str(m.get("text") or ""))]
        if not hit:
            continue
        hit.sort(key=_when)
        out[i] = [dict(m, text=str(m.get("text") or "")[:MAX_TEXT]) for m in (hit if cap is None else hit[-cap:])]
    return out


def _links_text(card):
    out = []
    for l in card.get("links") or []:
        if isinstance(l, dict) and l.get("name"):
            out.append("- %s: %s" % (l.get("name"), l.get("relation") or "linked"))
    return "\n".join(out) or "(none)"


def build_prompt(template, card, messages, owners=()):
    lines = []
    for m in messages:
        lines.append("[%s] %s | %s | %s\n%s" % (m["id"], day_of(m.get("said_at")) or "?", m.get("sender") or "?",
                                               m.get("room") or "chat", m["text"]))
    cur = {f: card.get(f) for f in sf.FIELDS if card.get(f) not in (None, "", [])}
    vals = {"NAME": str(card.get("name") or ""), "KIND": str(card.get("kind") or ""),
            "CURRENT": json.dumps(cur, ensure_ascii=False, indent=1) if cur else "(nothing yet)",
            "LINKS": _links_text(card), "OWNERS": ", ".join(owners) if owners else "none named",
            "MESSAGES": "\n\n".join(lines) or "(none)"}
    # one pass: message text is never scanned for tokens
    return re.sub(r"\{\{(NAME|KIND|CURRENT|LINKS|OWNERS|MESSAGES)\}\}", lambda m: vals[m.group(1)], template)


def parse_facts(raw):
    text = re.sub(r"```(?:json)?", "", raw or "").strip()
    a, b = text.find("{"), text.rfind("}")
    if a < 0 or b <= a:
        raise ValueError("no JSON object in the answer")
    data = json.loads(text[a:b + 1])
    facts = data.get("facts") if isinstance(data, dict) else None
    if not isinstance(facts, list):
        raise ValueError('the answer has no "facts" list')
    return facts


def _source(msg):
    return " ".join(p for p in (str(msg.get("room") or "chat"), str(msg.get("thread") or "")) if p)


_STOP = {"about", "their", "there", "which", "would", "could", "should", "other", "these", "those", "where",
         "while", "being", "after", "before"}
_PAST = re.compile(r"\b(?:used to|previously|former|ex-\w+|hate[sd]?|dislike[sd]?|quit|stopped|retired|resigned|"
                  r"gave up|no more|(?:was|were)(?! born)|had|did)\b", re.I)
_ABBREV = {"mr", "mrs", "ms", "dr", "st", "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct",
           "nov", "dec", "vs", "etc", "no"}


def past_or_negative(sentence):
    """Negation words from suggest_fields, plus a short list of past and opposite-meaning words. A word list only."""
    return sf._negative(sentence) or _PAST.search(_nfc(sentence).replace("\u2019", "'")) is not None


def tense_flip(fact, sentence):
    """True when the sentence has a past -ed form (worked, lived, loved) of a word the fact writes in the present."""
    have = re.findall(r"\w+", sentence.casefold())
    for w in re.findall(r"\w+", _nfc(fact).casefold()):
        if len(w) < 4 or w.endswith("ed"):
            continue
        for h in have:
            if h != w and h.endswith("ed") and h[:4] == w[:4] and not w.startswith(h):
                return True
    return False


def sentences_of(text):
    """The message cut at newlines and at . ! ? followed by a space and a capital or digit (not after Mr, Jan ...)."""
    out = []
    for line in _nfc(text).split("\n"):
        start = 0
        for m in re.finditer(r"[.!?]+\s+(?=[A-Z0-9\"'(])", line):
            word = re.findall(r"[A-Za-z]+", line[start:m.start()])
            if word and word[-1].lower() in _ABBREV and m.group().startswith("."):
                continue
            out.append(line[start:m.end()])
            start = m.end()
        out.append(line[start:])
    return [_norm(x) for x in out if _norm(x)]


def sentence_of(text, quote):
    """The sentence holding the quote; when the quote runs across sentences, those sentences joined. Falls back to
    the whole message."""
    sents = sentences_of(text)
    q = _norm(_nfc(quote)).casefold()
    for n in (1, 2, 3):
        for i in range(len(sents) - n + 1):
            joined = " ".join(sents[i:i + n])
            if q in joined.casefold():
                return joined
    return _norm(_nfc(text))


_RELATION_WORDS = {"companion", "partner", "husband", "wife", "son", "daughter", "mother", "father", "brother",
                   "sister"}


def _own_words(card):
    own = set()
    for n in names_of(card):
        own.update(re.findall(r"\w+", _nfc(n).casefold()))
    return own


def _same_word(a, b):
    """Equal, or one is the other plus s / es / ing / ed / d (or e dropped before ing)."""
    if a == b:
        return True
    for x, y in ((a, b), (b, a)):
        if y.startswith(x) and y[len(x):] in ("s", "es", "ing", "ed", "d"):
            return True
        if x.endswith("e") and y == x[:-1] + "ing":
            return True
    return False


def fact_tied(card, fact, message_text):
    """False when no content word of the fact (letters only, 4+ letters, not a stop word, not the person's name)
    occurs in the cited message. Light on purpose: it only catches a fact with nothing to do with the message;
    The owner sees every fact beside its quote."""
    own = _own_words(card)
    have = re.findall(r"[^\W\d_]+", _nfc(message_text).casefold())
    words = [w for w in re.findall(r"[^\W\d_]+", _nfc(fact).casefold())
             if len(w) >= 4 and w not in _STOP and w not in own]
    if not words:
        return False
    return any(_same_word(w, h) for w in words for h in have)


def restates_link(card, fact):
    """True when the fact only restates a link the card already shows: a linked person's name, a relation word, and
    no other content word of 5+ letters."""
    links = [l for l in (card.get("links") or []) if isinstance(l, dict) and l.get("name")]
    if not links:
        return False
    fact = _nfc(fact)
    words = re.findall(r"[^\W\d_]+", fact.casefold())
    fact_rel = {w for w in words if w in _RELATION_WORDS}
    matched = [l for l in links if name_in([str(l["name"])], fact)]
    if not matched or not fact_rel:
        return False
    # each relation word in the fact must be the relation of a linked person the fact names
    paired = set()
    for l in matched:
        paired |= set(re.findall(r"\w+", _nfc(str(l.get("relation") or "")).casefold())) & _RELATION_WORDS
    if not fact_rel <= paired:
        return False
    skip = set(_own_words(card)) | _RELATION_WORDS | _STOP
    for l in links:
        skip.update(re.findall(r"\w+", _nfc(str(l["name"])).casefold()))
        skip.update(re.findall(r"\w+", _nfc(str(l.get("relation") or "")).casefold()))
    return not any(len(w) >= 5 and w not in skip for w in words)


def about_someone_else(names, other_names, msg, sentence, owners=()):
    """True when the quoted sentence names another card, does not name this person, and this person did not write it."""
    if sender_is(names, msg.get("sender")) or name_in(names, sentence):
        return False
    mine = {_letters(n) for n in names_of({"name": "", "aka": names})}
    others = [n for n in list(other_names) + [" ".join(t) for t in _owner_tokens(owners)] if _letters(n) not in mine]
    return name_in(others, sentence)


def check_fact(card, by_id, item, names=None, other_names=(), owners=()):
    """(kind, entry, why). kind is 'fact' or 'field'; entry is None when it fails, with the reason in why."""
    if not isinstance(item, dict):
        return None, None, "not an object"
    msg = by_id.get(str(item.get("message_id")))
    if msg is None:
        return None, None, "the message is not one of this person's messages"
    quote, fact = item.get("quote"), item.get("fact")
    if not isinstance(quote, str) or not _norm(quote):
        return None, None, "no quote"
    quote = _norm(_nfc(quote))
    text = _norm(_nfc(msg["text"]))
    if _nfc(item["quote"]).strip() not in _nfc(msg["text"]):
        return None, None, "the quote is not word for word in the message"
    sentence = sentence_of(msg["text"], quote)
    names = names if names is not None else names_of(card)
    if not (name_in(names, msg["text"]) or sender_is(names, msg.get("sender"))):
        return None, None, "the person is not named in the message and did not write it"
    if about_someone_else(names, other_names, msg, sentence, owners):
        return None, None, "the quoted sentence is about someone else"
    if not isinstance(fact, str) or not 1 <= len(_norm(fact)) <= MAX_FACT:
        return None, None, "fact text must be 1 to %d characters" % MAX_FACT
    if about_owner(fact, msg, quote, card.get("name"), owners):
        return None, None, "fact about the owner"
    if item.get("field") is None and restates_link(card, fact):
        return None, None, "already shown on the card"
    if not fact_tied(card, fact, msg["text"]):
        return None, None, "the fact has nothing to do with the message"
    if tense_flip(fact, sentence) and item.get("field") != "care":
        return None, None, "the quoted sentence is in the past tense; the fact is not"
    base = {"quote": quote, "source": _source(msg)}
    field = item.get("field")
    if field is None:
        if past_or_negative(sentence):
            return None, None, "the quoted sentence is negative or past; check by hand"
        return "fact", dict(base, fact=_norm(fact), message_ids=[msg["id"]], said_on=day_of(msg.get("said_at"))), ""
    if field != "care" and past_or_negative(sentence):
        return None, None, "the quoted sentence is negative or past; check by hand"
    e, why = sf.check_suggestion(card, {"field": field, "value": item.get("value"), "quote": quote}, [text])
    if not e:
        return None, None, why
    out = dict(base, field=e["field"], value=e["value"], message_ids=[msg["id"]], said_on=day_of(msg.get("said_at")))
    out["quote"] = e["quote"]
    if e.get("inferred"):
        out["inferred"] = True
    return "field", out, ""


def roster_others(cards, i, owners=()):
    """Every name and aka of the other cards on the roster (not this card, not an owner)."""
    out = []
    for j, c in enumerate(cards):
        if j != i and isinstance(c, dict) and not is_owner_card(c, owners):
            out += names_of(c)
    return out


def find(cards, messages, model_cmd, timeout=600, only=None, limit_people=None, owners=()):
    with open(PROMPT_FILE, encoding="utf-8") as f:
        template = f.read()
    only_l = {str(o).casefold() for o in (only or [])}
    res = {"people_seen": 0, "facts": [], "field_suggestions": [], "rejected": [], "errors": []}
    seen = set()
    shared = shared_names(cards)
    for i, msgs in group_messages(cards, messages, owners, cap=None).items():
        card = cards[i]
        if only_l and str(card.get("name", "")).casefold() not in only_l and str(card.get("id", "")).casefold() not in only_l:
            continue
        if limit_people is not None and res["people_seen"] >= limit_people:
            break
        res["people_seen"] += 1
        who = {"person_id": card.get("id"), "name": card.get("name")}
        for start in range(0, len(msgs), MAX_PER_PERSON):   # a backlog is read in batches; nothing is dropped
            batch = msgs[start:start + MAX_PER_PERSON]
            by_id = {str(m["id"]): m for m in batch}
            try:
                text, _rec = wm.unwrap(wm.call_model(model_cmd, build_prompt(template, card, batch, owners), timeout))
                items = parse_facts(text)
            except Exception as e:                    # one person failing never stops the run
                res["errors"].append(dict(who, error=str(e)[:300] or type(e).__name__))
                continue
            for it in items:
                kind, entry, why = check_fact(card, by_id, it, own_names(card, shared), roster_others(cards, i, owners), owners)
                if not entry:
                    g = it if isinstance(it, dict) else {}
                    res["rejected"].append(dict(who, fact=g.get("fact"), field=g.get("field"), value=g.get("value"),
                                                quote=g.get("quote"), message_id=g.get("message_id"), why=why))
                    continue
                key = (str(card.get("id")), _norm(entry["quote"]).lower(), entry.get("field", ""),
                       json.dumps(entry.get("value"), sort_keys=True, default=str).lower(),
                       _norm(entry.get("fact", "")).lower())
                if key in seen:
                    continue
                seen.add(key)
                res["facts" if kind == "fact" else "field_suggestions"].append(dict(who, **entry))
    vals = {}
    for e in res["field_suggestions"]:
        if e["field"] in sf.SCALAR_FIELDS:
            vals.setdefault((str(e["person_id"]), e["field"]), set()).add(_norm(e["value"]).lower())
    kept = []
    for e in res["field_suggestions"]:
        if e["field"] in sf.SCALAR_FIELDS and len(vals[(str(e["person_id"]), e["field"])]) > 1:
            res["rejected"].append({"person_id": e["person_id"], "name": e["name"], "fact": None, "field": e["field"],
                                    "value": e["value"], "quote": e["quote"], "message_id": e["message_ids"][0],
                                    "why": "conflicting values for %s; check by hand" % e["field"]})
        else:
            kept.append(e)
    res["field_suggestions"] = kept
    counts = {}
    for r in res["rejected"]:
        counts[r["why"]] = counts.get(r["why"], 0) + 1
    res["rejected_by_reason"] = counts
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--messages", required=True, help="a JSON list of chat messages")
    ap.add_argument("--cards", required=True, help="a JSON list of people cards")
    ap.add_argument("--out", required=True, help="the found-facts JSON file to write")
    ap.add_argument("--model", default="claude-sonnet-5-5")
    ap.add_argument("--model-cmd", default=None, help="a whole command of your own (overrides --model)")
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--only", nargs="+", default=None, help="only people with these names (or ids)")
    ap.add_argument("--owner", action="append", default=[], metavar="NAME",
                    help="a person who owns this record (repeatable; also SCRIPTORIUM_OWNERS, comma-separated)")
    ap.add_argument("--limit-people", type=int, default=None, help="at most N people go to the model")
    a = ap.parse_args(argv)
    with open(a.messages, encoding="utf-8") as f:
        messages = json.load(f)
    with open(a.cards, encoding="utf-8") as f:
        cards = json.load(f)
    if not isinstance(messages, list) or not isinstance(cards, list):
        print("messages and cards files must each hold a JSON list", file=sys.stderr)
        return 2
    model_cmd = a.model_cmd or MODEL_CMD_TEMPLATE.format(model=wm.shlex.quote(a.model))
    owners = owners_from(a.owner, owners_env())
    res = find(cards, messages, model_cmd, a.timeout, a.only, a.limit_people, owners)
    doc = {"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
           "model": "custom command" if a.model_cmd else a.model, "people_seen": res["people_seen"],
           "facts": res["facts"], "field_suggestions": res["field_suggestions"], "rejected": res["rejected"],
           "rejected_by_reason": res["rejected_by_reason"], "errors": res["errors"]}
    wm.write_atomic(a.out, doc)
    print("%d people, %d facts, %d field suggestions, %d rejected, %d errors -> %s" % (
        res["people_seen"], len(res["facts"]), len(res["field_suggestions"]), len(res["rejected"]),
        len(res["errors"]), a.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
