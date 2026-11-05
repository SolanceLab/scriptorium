# Solance House — Copyright (c) 2026 House of Solance. All rights reserved. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""Review the field values the nightly run suggests.

    python3 review.py --out found list
    python3 review.py --out found --cards people accept N
    python3 review.py --out found dismiss N
    python3 review.py --out found remove-fact KEY N
    python3 review.py --out found edit-fact KEY N "new text"
    (remove-fact and edit-fact take --expect FINGERPRINT before the action: refused if fact N is no longer that fact)

`list` prints the pending suggestions, each with its number, the person, the value and the exact words it rests on.
`accept N` writes the value into OUT/fields/<key>.json (a file write_people never rewrites; the card in --cards must
exist) and marks the suggestion accepted. `dismiss N` marks it dismissed (it is not suggested again). Facts need no
review: they sit in facts/<key>.json; `remove-fact KEY N` deletes the Nth fact (counting from 1) of that person; `edit-fact KEY N TEXT` replaces
its text and marks it edited (its quote and source stay).
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import write_memories as wm  # noqa: E402
import suggest_fields as sf  # noqa: E402
import nightly  # noqa: E402


LOCK_WAIT = 30.0


def load(out):
    path = os.path.join(out, "suggestions.json")
    try:
        with open(path, encoding="utf-8") as f:
            sugs = json.load(f)
    except FileNotFoundError:
        return path, []
    if not isinstance(sugs, list):
        raise ValueError("%s is not a list" % path)
    return path, sugs


def find_pending(sugs, n):
    for s in sugs:
        if isinstance(s, dict) and s.get("n") == n:
            if s.get("status") != "pending":
                raise ValueError("suggestion %d is already %s" % (n, s.get("status")))
            return s
    raise ValueError("no suggestion numbered %d" % n)


def safe_key(key):
    try:
        return nightly.safe_key(key)
    except nightly.NightlyError as e:
        raise ValueError(str(e))


def apply_to_card(cards_dir, out, sug):
    """Keep an accepted value in OUT/fields/<key>.json; the card file is only checked, never rewritten."""
    key = safe_key(sug.get("person_id") or "")
    path = os.path.join(cards_dir, "person-%s.json" % key)
    if os.path.islink(path) or not os.path.isfile(path):
        raise ValueError("no card file %s" % path)
    with open(path, encoding="utf-8") as f:
        if not isinstance(json.load(f), dict):
            raise ValueError("%s is not a card" % path)
    fpath = os.path.join(out, "fields", key + ".json")
    try:
        with open(fpath, encoding="utf-8") as f:
            fields = json.load(f)
    except FileNotFoundError:
        fields = {}
    if not isinstance(fields, dict):
        raise ValueError("%s is not an object" % fpath)
    field, value = sug["field"], sug["value"]
    if field not in sf.FIELDS:
        raise ValueError("unknown field %r" % field)
    if field in sf.LIST_FIELDS:
        cur = fields.setdefault(field, [])
        if value not in cur:
            cur.append(value)
    else:
        fields[field] = value
    os.makedirs(os.path.dirname(fpath), exist_ok=True)
    wm.write_atomic(fpath, fields)


def fact_fp(f):
    """A short fingerprint of a fact's own words (quote and text), so a change can say which fact it means."""
    import hashlib
    q = f.get("quote") if isinstance(f, dict) and isinstance(f.get("quote"), str) else ""
    t = f.get("fact") if isinstance(f, dict) and isinstance(f.get("fact"), str) else ""
    return hashlib.sha1((q + "\0" + t).encode("utf-8")).hexdigest()[:12]


def check_expect(facts, n, expect):
    if expect is not None and fact_fp(facts[n - 1]) != expect:
        raise ValueError("fact %d has changed since you loaded it; reload and try again" % n)


def remove_fact(out, key, n, expect=None):
    key = safe_key(key)
    path = os.path.join(out, "facts", key + ".json")
    try:
        with open(path, encoding="utf-8") as f:
            facts = json.load(f)
    except FileNotFoundError:
        raise ValueError("no facts file %s" % path)
    if not isinstance(facts, list):
        raise ValueError("%s is not a list" % path)
    if not 1 <= n <= len(facts):
        raise ValueError("%s has %d facts; no fact %d" % (key, len(facts), n))
    check_expect(facts, n, expect)
    del facts[n - 1]
    wm.write_atomic(path, facts)


def edit_fact(out, key, n, text, expect=None):
    key = safe_key(key)
    text = " ".join(str(text).split())
    if not text:
        raise ValueError("the fact text is empty")
    path = os.path.join(out, "facts", key + ".json")
    try:
        with open(path, encoding="utf-8") as f:
            facts = json.load(f)
    except FileNotFoundError:
        raise ValueError("no facts file %s" % path)
    if not isinstance(facts, list):
        raise ValueError("%s is not a list" % path)
    if not 1 <= n <= len(facts) or not isinstance(facts[n - 1], dict):
        raise ValueError("%s has %d facts; no fact %d" % (key, len(facts), n))
    check_expect(facts, n, expect)
    facts[n - 1]["fact"] = text
    facts[n - 1]["status"] = "edited"
    wm.write_atomic(path, facts)


class Busy(Exception):
    pass


class RunLock:
    """The nightly run's own lock file (OUT/.lock): wait up to 30 s, then give up."""

    def __init__(self, out, wait=30.0):
        self.path, self.wait, self.fd, self.fallback = os.path.join(out, ".lock"), wait, None, False

    def __enter__(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        end = time.monotonic() + self.wait
        try:
            import fcntl
        except ImportError:                     # no fcntl (Windows): the same <lock>.run file nightly.py creates
            self.fallback = True
            while True:
                try:
                    self.fd = nightly.create_run_file(self.path + ".run")
                    return self
                except FileExistsError:
                    if time.monotonic() >= end:
                        raise Busy("the nightly run is busy; try again")
                    time.sleep(0.2)
        self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        while True:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except OSError:
                if time.monotonic() >= end:
                    os.close(self.fd)
                    self.fd = None
                    raise Busy("the nightly run is busy; try again")
                time.sleep(0.2)

    def __exit__(self, *exc):
        if self.fd is not None:
            os.close(self.fd)
            if self.fallback:
                try:
                    os.remove(self.path + ".run")
                except OSError:
                    pass


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", required=True, help="the nightly run's folder")
    ap.add_argument("--cards", default=None, help="the folder of person cards (needed for accept)")
    ap.add_argument("--expect", default=None, help="for remove-fact and edit-fact: the fact's fingerprint; a different fact at that number is refused")
    ap.add_argument("action", choices=["list", "accept", "dismiss", "remove-fact", "edit-fact"])
    ap.add_argument("rest", nargs="*", help="N for accept and dismiss; KEY N for remove-fact; KEY N TEXT for edit-fact")
    a = ap.parse_args(argv)
    a.number = a.key = a.text = None
    if a.rest[:1] == ["--"]:                    # older Pythons keep the "--" that guards a value starting with a dash
        a.rest = a.rest[1:]
    try:
        if a.action in ("accept", "dismiss") and len(a.rest) == 1:
            a.number = int(a.rest[0])
        elif a.action == "remove-fact" and len(a.rest) == 2:
            a.key, a.number = a.rest[0], int(a.rest[1])
        elif a.action == "list" and not a.rest:
            pass
        elif a.action == "edit-fact" and len(a.rest) == 3:
            a.key, a.number, a.text = a.rest[0], int(a.rest[1]), a.rest[2]
        else:
            raise ValueError
    except ValueError:
        ap.error("%s needs %s" % (a.action, "KEY N TEXT" if a.action == "edit-fact" else "KEY N" if a.action == "remove-fact" else "a number" if a.action != "list" else "nothing more"))
    if a.action == "accept" and not a.cards:
        ap.error("accept needs --cards (the folder of person cards)")
    try:
        if a.action == "list":
            return act(a)
        with RunLock(a.out, LOCK_WAIT):
            return act(a)
    except Busy as e:
        print("review: %s" % e, file=sys.stderr)
        return 1
    except (ValueError, OSError) as e:
        print("review: %s" % e, file=sys.stderr)
        return 1


def act(a):
    if a.action == "edit-fact":
        edit_fact(a.out, a.key, a.number, a.text, a.expect)
        print("edited fact %d of %s" % (a.number, a.key))
        return 0
    if a.action == "remove-fact":
        remove_fact(a.out, a.key, a.number, a.expect)
        print("removed fact %d of %s" % (a.number, a.key))
        return 0
    path, sugs = load(a.out)
    if a.action == "list":
        pend = [s for s in sugs if isinstance(s, dict) and s.get("status") == "pending"]
        if not pend:
            print("nothing waiting")
        for s in pend:
            print("%d. %s: %s = %s\n     \"%s\"  (%s, %s)" % (
                s["n"], s.get("name"), s.get("field"), json.dumps(s.get("value"), ensure_ascii=False),
                s.get("quote"), s.get("source"), s.get("said_on")))
        return 0
    s = find_pending(sugs, a.number)
    if a.action == "accept":
        apply_to_card(a.cards, a.out, s)      # card first: a failure leaves the suggestion pending
        s["status"] = "accepted"
    else:
        s["status"] = "dismissed"
    wm.write_atomic(path, sugs)
    print("%s %d" % (s["status"], a.number))
    return 0


if __name__ == "__main__":
    sys.exit(main())
