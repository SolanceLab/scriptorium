# Solance House — Copyright (c) 2026 House of Solance. All rights reserved. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""The roster fills itself: a nightly run of "found in chats" over the messages that are new since the last run.

    python3 nightly.py --export conversations.json --cards people --out found [--owner NAME ...]
                       [--model claude-sonnet-5-5] [--model-cmd CMD] [--timezone Asia/Jakarta]

--export  a Claude-format conversations.json (the same file the writers read).
--cards   the folder of person-<key>.json cards that write_people.py made.
--out     the folder this run keeps its results in:
            facts/<key>.json   facts found for each person, with the exact words (appended, never repeated)
            suggestions.json   values for a card's fixed fields, waiting for `review.py accept`
            cursor.json        {"last_said_at": ..., "last_ids": [...]}: the newest time already read, and the ids read at that time
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

CARD_PREFIX, CARD_SUFFIX = "person-", ".json"


class NightlyError(Exception):
    pass


def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except (OSError, ValueError) as e:
        raise NightlyError("cannot read %s: %s" % (path, e))


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


STALE_LOCK_SECONDS = 6 * 3600


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True                     # exists but is not ours (or the check is unavailable): treat as alive
    return True


def _lock_is_stale(path):
    """A leftover run-file is stale when its pid is gone, or (pid unreadable) when it is older than six hours."""
    try:
        age = time.time() - os.path.getmtime(path)
        with open(path, encoding="utf-8") as f:
            text = f.read().strip()
    except OSError:
        return False
    try:
        pid = int(text)
    except ValueError:
        return age > STALE_LOCK_SECONDS
    if os.name == "nt":
        return age > STALE_LOCK_SECONDS
    return not _pid_alive(pid)


def create_run_file(path):
    """Create-exclusive <path> holding our pid; a stale one (crashed run) is removed first. FileExistsError if live."""
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if _lock_is_stale(path):
                try:
                    os.remove(path)
                except OSError:
                    pass
                continue
            raise
        os.write(fd, str(os.getpid()).encode())
        return fd
    raise FileExistsError(path)


class Lock:
    """One run at a time: an exclusive lock on OUT/.lock, released by the system if the run dies."""

    def __init__(self, path):
        self.path, self.fd, self.fallback = path, None, False

    def __enter__(self):
        try:
            import fcntl
        except ImportError:                     # no fcntl (Windows): a plain create-exclusive file
            self.fallback = True
            try:
                self.fd = create_run_file(self.path + ".run")
            except FileExistsError:
                raise NightlyError("another run holds the lock (%s.run)" % self.path)
            return self
        self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(self.fd)
            self.fd = None
            raise NightlyError("another run is in progress (lock: %s)" % self.path)
        return self

    def __exit__(self, *exc):
        if self.fd is not None:
            os.close(self.fd)
            if self.fallback:
                try:
                    os.remove(self.path + ".run")
                except OSError:
                    pass


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


def run(export, cards_dir, out, owners=(), model_cmd=None, model=wm.DEFAULT_MODEL, timezone=None, timeout=600):
    """Returns (summary line, ok)."""
    fc.DAY_TZ = timezone or None            # no zone: a fact's day is the timestamp's own calendar date
    os.makedirs(os.path.join(out, "facts"), exist_ok=True)
    model_cmd = model_cmd or fc.MODEL_CMD_TEMPLATE.format(model=wm.shlex.quote(model))
    with Lock(os.path.join(out, ".lock")):
        cursor_path = os.path.join(out, "cursor.json")
        cursor = read_json(cursor_path, None)
        last = cursor.get("last_said_at") if isinstance(cursor, dict) else None
        messages = load_messages(export)
        cards = load_person_cards(cards_dir, out)
        last_ids = cursor.get("last_ids") if isinstance(cursor, dict) and isinstance(cursor.get("last_ids"), list) else []
        new, undated = newer_than(messages, last, last_ids)
        since = ("after %s" % last) if last else "no cursor yet, so all messages"
        note = ("; %d messages had no time and were not read; cursor not moved" % undated) if undated else ""
        if not new:
            return "0 new messages (%s); nothing to do%s" % (since, note), not undated
        if not cards:
            return "%d new messages (%s) but no person cards in %s; cursor not moved%s" % (
                len(new), since, cards_dir, note), False
        res = fc.find(cards, new, model_cmd, timeout, owners=owners)
        added_facts = added_sugs = 0
        by_person = {}
        for e in res["facts"]:
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
            wm.write_atomic(path, have)
        sug_path = os.path.join(out, "suggestions.json")
        sugs = read_json(sug_path, [])
        if not isinstance(sugs, list):
            raise NightlyError("%s is not a list" % sug_path)
        seen = {_key_sug(s) for s in sugs if isinstance(s, dict)}
        n = max([s.get("n", 0) for s in sugs if isinstance(s, dict) and isinstance(s.get("n"), int)] or [0])
        for e in res["field_suggestions"]:
            if _key_sug(e) not in seen:
                seen.add(_key_sug(e))
                n += 1
                sugs.append(dict(e, n=n, status="pending"))
                added_sugs += 1
        if added_sugs or not os.path.exists(sug_path):
            wm.write_atomic(sug_path, sugs)
        line = "%d messages read (%s), %d people, %d new facts, %d new suggestions, %d rejected" % (
            len(new), since, res["people_seen"], added_facts, added_sugs, len(res["rejected"]))
        if res["errors"]:
            names = ", ".join(str(e.get("name")) for e in res["errors"])
            return line + "; FAILED for %s (%s); cursor not moved%s" % (names, res["errors"][0]["error"][:120], note), False
        if undated:
            return line + note, False
        top = max(fc._when(m) for m in new)
        at_top = [m for m in new if fc._when(m) == top]
        newest = at_top[0]["said_at"]
        ids = {str(m["id"]) for m in at_top}
        if last and fc._parse_when(last) == top:      # the boundary did not move: keep the ids already read
            ids |= {str(i) for i in last_ids}
        wm.write_atomic(cursor_path, {"last_said_at": newest, "last_ids": sorted(ids),
                                      "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")})
        return line + "; cursor now %s" % newest, True


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--export", required=True, help="a Claude-format conversations.json")
    ap.add_argument("--cards", required=True, help="the folder of person-<key>.json cards")
    ap.add_argument("--out", required=True, help="the folder for facts/, suggestions.json and cursor.json")
    ap.add_argument("--owner", action="append", default=[], metavar="NAME",
                    help="a person who owns this record (repeatable; also SCRIPTORIUM_OWNERS, comma-separated)")
    ap.add_argument("--model", default=wm.DEFAULT_MODEL, help="pin one (claude-sonnet-5-5) for a reported run")
    ap.add_argument("--model-cmd", default=os.environ.get("MEMORY_WRITER_CMD"),
                    help="a whole command of your own instead of the default (overrides --model)")
    ap.add_argument("--timezone", default=os.environ.get("MEMORY_WRITER_TZ") or None,
                    help="IANA time zone for the day a fact was said, e.g. Asia/Jakarta (default: the timestamp's own date)")
    ap.add_argument("--timeout", type=int, default=600)
    a = ap.parse_args(argv)
    if a.timezone:
        from zoneinfo import ZoneInfo
        try:
            ZoneInfo(a.timezone)
        except Exception:
            ap.error("unknown time zone %r (use an IANA name such as Asia/Jakarta)" % a.timezone)
    owners = fc.owners_from(a.owner, fc.owners_env())
    try:
        line, ok = run(a.export, a.cards, a.out, owners, a.model_cmd, a.model, a.timezone, a.timeout)
    except NightlyError as e:
        print("nightly: %s" % e, file=sys.stderr)
        return 1
    print(("nightly: " if ok else "nightly FAILED: ") + line)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
