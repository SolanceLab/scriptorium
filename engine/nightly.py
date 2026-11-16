# Scriptorium — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""The roster fills itself: a nightly run of "found in chats" over the messages that are new since the last run.

    python3 nightly.py --export conversations.json --cards people --out found [--owner NAME ...]
                       [--model claude-sonnet-5-5] [--model-cmd CMD] [--timezone Asia/Jakarta]

--export  a Claude-format conversations.json (the same file the writers read).
--cards   the folder of person-<key>.json cards that write_people.py made.
--source / --sink   your own store instead of those files, as module:attr (see CONTRACT.md, "Plug in your own store").
--out     the folder this run keeps its results in:
            facts/<key>.json   facts found for each person, with the exact words (appended, never repeated)
            suggestions.json   values for a card's fixed fields, waiting for `review.py accept`
            cursor.json        {"last_said_at": ..., "last_ids": [...]}: the newest time already read, and the ids read at that time
            status.json        what the last run did; rejected.jsonl: every item turned away, with the reason
--owner   a person who owns this record (repeatable; also SCRIPTORIUM_OWNERS, comma-separated). Facts about an
          owner are never kept.

Only messages newer than the cursor go to the model, per person they name (in batches of 60 messages). The cursor moves forward only after
every result is written and no person failed; a failed run leaves it where it was, so the next run reads the same
messages again (results already written are not repeated). A lock file keeps two runs from overlapping.
"""
import argparse
import datetime as dt
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import write_memories as wm  # noqa: E402
import found_in_chats as fc  # noqa: E402
import suggest_fields as sf  # noqa: E402
import roster as rr  # noqa: E402

CARD_PREFIX, CARD_SUFFIX = "person-", ".json"


NightlyError = rr.RosterError
read_json = rr.read_json


def load_messages(export_path):
    """Every message of a Claude-format export as {id, sender, room, thread, said_at, text}, read by the writer's own
    loader. The export's own speaker stays the sentinel sender "owner" (it is never turned into a name, so it can
    never match a card). An id that appears twice in the export is dropped (it could not be told apart)."""
    convs = read_json(export_path, None)
    if not isinstance(convs, list):
        raise NightlyError("%s is not a list of conversations" % export_path)
    out, seen = [], {}
    for conv in convs:
        if not isinstance(conv, dict):
            continue
        cid, title, lines = wm.claude_lines(conv)
        for lid, sender, text, at in lines:
            if sender == "user":                # the export's own speaker is the owner
                sender = "owner"
            seen[lid] = seen.get(lid, 0) + 1
            out.append({"id": lid, "sender": sender, "room": "chat", "thread": title or cid, "said_at": at,
                        "text": text})
    return [m for m in out if seen[m["id"]] == 1]


def load_person_cards(cards_dir, out=None):
    """person-<key>.json files into found_in_chats' card shape. id is the key from the file name; fixed fields come
    from the card's own `fields` object and from OUT/fields/<key>.json (where review.py accept writes them, a file
    write_people never touches)."""
    cards = []
    if not os.path.isdir(cards_dir):
        return cards
    for name in sorted(os.listdir(cards_dir)):
        path = os.path.join(cards_dir, name)
        if not (name.startswith(CARD_PREFIX) and name.endswith(CARD_SUFFIX)) or os.path.islink(path):
            continue
        try:
            doc = read_json(path, {})
        except NightlyError:
            continue
        if not isinstance(doc, dict):
            continue
        key = name[len(CARD_PREFIX):-len(CARD_SUFFIX)]
        mems = [m for m in doc.get("memories") or [] if isinstance(m, dict)]
        pname = next((str(m["person"]) for m in mems if m.get("person")), "")
        if not pname:
            pname = str(doc.get("title") or "").replace(" (person card)", "").strip()
        if not pname:
            continue
        fields = dict(doc.get("fields")) if isinstance(doc.get("fields"), dict) else {}
        aka = list(doc.get("aka")) if isinstance(doc.get("aka"), list) else []
        if isinstance(fields.get("aka"), list):
            aka += fields["aka"]
        if out:
            try:
                accepted = read_json(os.path.join(out, "fields", key + ".json"), {})
            except NightlyError:
                accepted = {}
            if isinstance(accepted, dict):
                if isinstance(accepted.get("aka"), list):
                    aka += [a for a in accepted["aka"] if a not in aka]
                fields.update({k: v for k, v in accepted.items() if k != "aka"})
        card = {"id": key, "name": pname, "kind": str(doc.get("kind") or "person"), "aka": aka,
                "links": doc.get("links") if isinstance(doc.get("links"), list) else []}
        for f in sf.FIELDS:
            if f == "aka":
                continue
            v = fields.get(f) if f in fields else doc.get(f)
            if v not in (None, "", []):
                card[f] = v
        cards.append(card)
    return cards


def newer_than(messages, last, last_ids=()):
    """(messages newer than last, count of messages with no readable time). last None = all of them. A message at
    exactly the cursor's time counts as new unless its id is in last_ids (already read at that time)."""
    cut = fc._parse_when(last) if last else None
    done = {str(i) for i in last_ids or ()}
    kept, undated = [], 0
    for m in messages:
        when = fc._parse_when(m.get("said_at"))
        if when is None:
            undated += 1
            continue
        if cut is None or when > cut or (when == cut and str(m["id"]) not in done):
            kept.append(m)
    return kept, undated


Lock, create_run_file, STALE_LOCK_SECONDS = rr.Lock, rr.create_run_file, rr.STALE_LOCK_SECONDS   # review.py and the tests use these here


def _key_fact(e):
    norm = lambda v: " ".join(str(v or "").split()).lower()
    return (str(e.get("person_id")), norm(e.get("quote")), norm(e.get("fact")))


def _key_sug(e):
    return (str(e.get("person_id")), e.get("field"), json.dumps(e.get("value"), sort_keys=True, default=str).lower())


def safe_key(key):
    key = str(key)
    if not key or os.sep in key or (os.altsep and os.altsep in key) or key.startswith("."):
        raise NightlyError("unsafe card key %r" % key)
    return key


class FileSource:
    """The default source: a Claude-format export and a folder of person cards."""

    def __init__(self, export, cards_dir, out=None):
        self.export, self.cards_dir, self.out = export, cards_dir, out

    def read(self, cursor):
        messages = load_messages(self.export)
        new, _ = newer_than(messages, cursor.get("last_said_at"), cursor.get("last_ids"))
        undated = [m for m in messages if fc._parse_when(m.get("said_at")) is None]   # the runner counts and reports them
        return new + undated

    def cards(self):
        return load_person_cards(self.cards_dir, self.out)


class FileSink:
    """The default sink: OUT/facts/<key>.json and OUT/suggestions.json, appended and never repeated."""

    def __init__(self, out):
        self.out = out

    def write(self, facts, suggestions):
        out = self.out
        os.makedirs(os.path.join(out, "facts"), exist_ok=True)
        added_facts = added_sugs = skipped = 0
        by_person = {}
        for e in facts:
            by_person.setdefault(safe_key(e["person_id"]), []).append(e)
        for key, entries in by_person.items():
            path = os.path.join(out, "facts", key + ".json")
            have = read_json(path, [])
            if not isinstance(have, list):
                raise NightlyError("%s is not a list" % path)
            seen = {_key_fact(h) for h in have if isinstance(h, dict)}
            for e in entries:
                if _key_fact(e) not in seen:
                    seen.add(_key_fact(e))
                    have.append(e)
                    added_facts += 1
                else:
                    skipped += 1
            wm.write_atomic(path, have)
        sug_path = os.path.join(out, "suggestions.json")
        sugs = read_json(sug_path, [])
        if not isinstance(sugs, list):
            raise NightlyError("%s is not a list" % sug_path)
        seen = {_key_sug(s) for s in sugs if isinstance(s, dict)}
        n = max([s.get("n", 0) for s in sugs if isinstance(s, dict) and isinstance(s.get("n"), int)] or [0])
        for e in suggestions:
            if _key_sug(e) not in seen:
                seen.add(_key_sug(e))
                n += 1
                sugs.append(dict(e, n=n, status="pending"))
                added_sugs += 1
            else:
                skipped += 1
        if added_sugs or not os.path.exists(sug_path):
            wm.write_atomic(sug_path, sugs)
        return {"stored": added_facts + added_sugs, "skipped": skipped, "rejected": [], "deferred": 0,
                "facts_stored": added_facts, "suggestions_stored": added_sugs}


def run(export, cards_dir, out, owners=(), model_cmd=None, model=wm.DEFAULT_MODEL, timezone=None, timeout=600,
        prompt_path=None, source=None, sink=None, settle_minutes=0, per_person_cap=None):
    """Returns (summary line, ok). source and sink replace the export/cards-folder and results-folder defaults.
    Unlike the roster runner's own defaults (10 minutes, 60 per person), this command waits for nothing and caps
    nothing unless asked."""
    fc.DAY_TZ = timezone or None            # no zone: a fact's day is the timestamp's own calendar date
    os.makedirs(out, exist_ok=True)
    model_cmd = model_cmd or fc.MODEL_CMD_TEMPLATE.format(model=wm.shlex.quote(model))
    source = source or FileSource(export, cards_dir, out)
    sink = sink or FileSink(out)
    st = rr.run_roster(source, sink, out, model_cmd=model_cmd, owners=owners, prompt_path=prompt_path,
                       settle_minutes=settle_minutes, per_person_cap=per_person_cap, timeout=timeout,
                       lock_path=os.path.join(out, ".lock"))
    since = ("after %s" % st["cursor_before"]) if st["cursor_before"] else "no cursor yet, so all messages"
    note = ("; %d messages had no time and were not read; cursor not moved" % st["undated"]) if st["undated"] else ""
    waits = ("; " + "; ".join(n for n in st["notes"] if "wait for the next run" in n)) if st["held"] or st["waiting_to_settle"] else ""
    reason = st["reason"]
    if reason == "locked":
        raise NightlyError(st["error"])
    if reason in ("nothing_new", "undated") and not st["messages"]:
        return "0 new messages (%s); nothing to do%s%s" % (since, note, waits), st["ok"]
    if reason == "no_cards":
        return "%d new messages (%s) but no person cards in %s; cursor not moved%s" % (
            st["messages"], since, cards_dir, note), False
    line = "%d messages read (%s), %d people, %s new facts, %s new suggestions, %d rejected" % (
        st["messages"], since, st["people_seen"], st["facts_stored"] or 0, st["suggestions_stored"] or 0, st["rejected"])
    if reason == "engine_errors":
        return line + "; FAILED for %s (%s); cursor not moved%s" % (
            ", ".join(str(e.get("name")) for e in st["errors"]), st["errors"][0]["error"][:120], note), False
    if reason == "undated":
        return line + note, False
    if not st["ok"]:
        return line + "; FAILED (%s); cursor not moved" % st["error"], False
    return line + waits + ("; cursor now %s" % st["cursor_after"] if st["cursor_moved"] else "; cursor not moved"), True


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--export", default=None, help="a Claude-format conversations.json (not needed with --source)")
    ap.add_argument("--cards", default=None, help="the folder of person-<key>.json cards (not needed with --source)")
    ap.add_argument("--out", required=True, help="the folder for facts/, suggestions.json, cursor.json, status.json and rejected.jsonl")
    ap.add_argument("--source", default=None, metavar="MODULE:ATTR",
                    help="your own message source (read(cursor) and cards()) instead of --export/--cards; see CONTRACT.md")
    ap.add_argument("--sink", default=None, metavar="MODULE:ATTR",
                    help="your own place to save results (write(facts, suggestions)) instead of the folder in --out")
    ap.add_argument("--settle-minutes", type=int, default=0,
                    help="messages newer than this many minutes wait for the next run (default 0: none)")
    ap.add_argument("--per-person-cap", type=int, default=None, metavar="N",
                    help="read at most N messages per person per run; the rest wait for the next run (default: no cap)")
    ap.add_argument("--owner", action="append", default=[], metavar="NAME",
                    help="a person who owns this record (repeatable; also SCRIPTORIUM_OWNERS, comma-separated)")
    ap.add_argument("--model", default=wm.DEFAULT_MODEL, help="pin one (claude-sonnet-5-5) for a reported run")
    ap.add_argument("--model-cmd", default=os.environ.get("MEMORY_WRITER_CMD"),
                    help="a whole command of your own instead of the default (overrides --model)")
    ap.add_argument("--timezone", default=os.environ.get("MEMORY_WRITER_TZ") or None,
                    help="IANA time zone for the day a fact was said, e.g. Asia/Jakarta (default: the timestamp's own date)")
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--found-prompt", default=None, help="a prompt file of your own for the roster step")
    a = ap.parse_args(argv)
    if not a.source and not (a.export and a.cards):
        ap.error("give --export and --cards, or your own --source")
    if a.settle_minutes < 0 or (a.per_person_cap is not None and a.per_person_cap < 1):
        ap.error("--settle-minutes must be 0 or more and --per-person-cap 1 or more")
    if a.timezone:
        from zoneinfo import ZoneInfo
        try:
            ZoneInfo(a.timezone)
        except Exception:
            ap.error("unknown time zone %r (use an IANA name such as Asia/Jakarta)" % a.timezone)
    owners = fc.owners_from(a.owner, fc.owners_env())
    try:
        source = rr.load_ref(a.source, ("read", "cards")) if a.source else None
        sink = rr.load_ref(a.sink, ("write",)) if a.sink else None
        line, ok = run(a.export, a.cards, a.out, owners, a.model_cmd, a.model, a.timezone, a.timeout, a.found_prompt,
                       source, sink, a.settle_minutes, a.per_person_cap)
    except NightlyError as e:
        print("nightly: %s" % e, file=sys.stderr)
        return 1
    print(("nightly: " if ok else "nightly FAILED: ") + line)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
