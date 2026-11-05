# Scriptorium — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""The nightly runner and the review command, with a fake model and invented people.
Run: python3 -m unittest test_nightly -v"""
import builtins
import contextlib
from unittest import mock
import io
import json
import os
import time
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
for _d in (os.path.join(HERE, "..", "writer"), os.path.join(HERE, "..", "engine")):
    if os.path.isdir(_d):
        sys.path.insert(0, _d)
import found_in_chats as fc  # noqa: E402
import nightly  # noqa: E402
import review  # noqa: E402

# The fake model answers with every canned item whose message id is in the prompt it was given.
FAKE = '''import json, re, sys
prompt = sys.stdin.read()
ids = set(re.findall(r"^\\[([^\\]]+)\\] ", prompt, re.M))
canned = json.load(open(%r))
print(json.dumps({"facts": [c for c in canned if c["message_id"] in ids]}))
'''
FAILING = "import sys\nsys.stdin.read()\nsys.exit(3)\n"

NEW = "Nate keeps bees these days."
OLD = "Nate works as a nurse. He lives in Lisbon."


def export_doc(*messages):
    return [{"uuid": "c1", "name": "Chat one", "created_at": "2026-10-01T09:00:00Z",
             "chat_messages": [{"uuid": mid, "sender": "human", "text": text, "created_at": at}
                               for mid, text, at in messages]}]


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.addCleanup(self._tmp.cleanup)
        self.cards = os.path.join(self.tmp, "people")
        self.out = os.path.join(self.tmp, "found")
        os.makedirs(self.cards)
        self.export = os.path.join(self.tmp, "conversations.json")
        self.card_file = os.path.join(self.cards, "person-nate.json")
        self.write(self.card_file, {"title": "Nate (person card)", "memories": [{"person": "Nate"}]})
        self.canned = [
            {"fact": "Works as a nurse.", "quote": "Nate works as a nurse", "message_id": "m1"},
            {"fact": "Lives in Lisbon.", "quote": "He lives in Lisbon", "message_id": "m1", "field": "place",
             "value": "Lisbon"},
            {"fact": "Keeps bees.", "quote": "Nate keeps bees", "message_id": "m2"}]
        self.write_export(("m1", OLD, "2026-10-01T10:00:00Z"))

    def write(self, path, obj):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f)

    def write_export(self, *messages):
        self.write(self.export, export_doc(*messages))

    def cmd(self, body=None):
        canned = os.path.join(self.tmp, "canned.json")
        self.write(canned, self.canned)
        path = os.path.join(self.tmp, "fake.py")
        with open(path, "w") as f:
            f.write(body if body is not None else FAKE % canned)
        return "%s %s" % (sys.executable, path)

    def nightly(self, body=None, extra=(), env=None):
        argv = ["--export", self.export, "--cards", self.cards, "--out", self.out, "--model-cmd", self.cmd(body),
                *extra]
        buf, err = io.StringIO(), io.StringIO()
        old = {k: os.environ.get(k) for k in (env or {})}
        os.environ.update(env or {})
        try:
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
                rc = nightly.main(argv)
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        return rc, buf.getvalue() + err.getvalue()

    def read(self, *parts):
        with open(os.path.join(self.out, *parts), encoding="utf-8") as f:
            return json.load(f)

    def review(self, *args):
        buf, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            rc = review.main(["--out", self.out, "--cards", self.cards, *args])
        return rc, buf.getvalue() + err.getvalue()


class Cursor(Base):
    def test_missing_cursor_reads_all_and_says_so(self):
        rc, text = self.nightly()
        self.assertEqual(rc, 0, text)
        self.assertIn("no cursor yet, so all messages", text)
        self.assertEqual(self.read("cursor.json")["last_said_at"], "2026-10-01T10:00:00Z")
        self.assertEqual([f["fact"] for f in self.read("facts", "nate.json")], ["Works as a nurse."])

    def test_second_run_reads_only_newer_messages(self):
        self.nightly()
        self.write_export(("m1", OLD, "2026-10-01T10:00:00Z"), ("m2", NEW, "2026-10-02T10:00:00Z"))
        rc, text = self.nightly()
        self.assertEqual(rc, 0, text)
        self.assertIn("1 messages read", text)
        self.assertEqual([f["fact"] for f in self.read("facts", "nate.json")], ["Works as a nurse.", "Keeps bees."])
        self.assertEqual(self.read("cursor.json")["last_said_at"], "2026-10-02T10:00:00Z")
        rc, text = self.nightly()
        self.assertIn("0 new messages", text)

    def test_cursor_unchanged_when_the_engine_fails(self):
        self.nightly()
        before = self.read("cursor.json")
        self.write_export(("m1", OLD, "2026-10-01T10:00:00Z"), ("m2", NEW, "2026-10-02T10:00:00Z"))
        rc, text = self.nightly(body=FAILING)
        self.assertNotEqual(rc, 0)
        self.assertIn("cursor not moved", text)
        self.assertEqual(self.read("cursor.json"), before)

    def test_no_cursor_file_after_a_failed_first_run(self):
        rc, _ = self.nightly(body=FAILING)
        self.assertNotEqual(rc, 0)
        self.assertFalse(os.path.exists(os.path.join(self.out, "cursor.json")))

    def test_rerun_after_failure_adds_nothing_twice(self):
        self.nightly()
        os.remove(os.path.join(self.out, "cursor.json"))
        self.nightly()
        self.assertEqual(len(self.read("facts", "nate.json")), 1)
        self.assertEqual(len(self.read("suggestions.json")), 1)


class Results(Base):
    def test_suggestions_pending_then_accept_writes_card_fields(self):
        self.nightly()
        sugs = self.read("suggestions.json")
        self.assertEqual([(s["field"], s["value"], s["status"]) for s in sugs], [("place", "Lisbon", "pending")])
        rc, text = self.review("list")
        self.assertEqual(rc, 0)
        self.assertIn("1. Nate: place", text)
        rc, text = self.review("accept", "1")
        self.assertEqual(rc, 0, text)
        self.assertEqual(self.read("fields", "nate.json"), {"place": "Lisbon"})
        self.assertEqual(self.read("suggestions.json")[0]["status"], "accepted")
        self.assertIn("nothing waiting", self.review("list")[1])
        self.assertNotEqual(self.review("accept", "1")[0], 0)           # not twice
        # the accepted value now counts as already on the card
        self.assertEqual(nightly.load_person_cards(self.cards, self.out)[0]["place"], "Lisbon")

    def test_dismiss_marks_dismissed_and_it_does_not_come_back(self):
        self.nightly()
        rc, _ = self.review("dismiss", "1")
        self.assertEqual(rc, 0)
        self.assertEqual(self.read("suggestions.json")[0]["status"], "dismissed")
        os.remove(os.path.join(self.out, "cursor.json"))
        self.nightly()
        self.assertEqual(len(self.read("suggestions.json")), 1)
        self.assertEqual(self.read("suggestions.json")[0]["status"], "dismissed")

    def test_accept_without_card_leaves_it_pending(self):
        self.nightly()
        os.remove(self.card_file)
        rc, _ = self.review("accept", "1")
        self.assertNotEqual(rc, 0)
        self.assertEqual(self.read("suggestions.json")[0]["status"], "pending")

    def test_list_field_appends(self):
        sug = {"person_id": "nate", "field": "aka", "value": "Nat"}
        review.apply_to_card(self.cards, self.out, sug)
        review.apply_to_card(self.cards, self.out, sug)
        self.assertEqual(self.read("fields", "nate.json"), {"aka": ["Nat"]})


class Lock(Base):
    def test_lock_prevents_overlap(self):
        os.makedirs(self.out)
        with nightly.Lock(os.path.join(self.out, ".lock")):
            rc, text = self.nightly()
            self.assertNotEqual(rc, 0)
            self.assertIn("another run", text)
        self.assertFalse(os.path.exists(os.path.join(self.out, "cursor.json")))
        rc, text = self.nightly()                                          # released afterwards
        self.assertEqual(rc, 0, text)


class ReviewLock(Base):
    def test_accept_waits_then_refuses_while_the_nightly_run_holds_the_lock(self):
        rc, text = self.nightly()
        self.assertEqual(rc, 0, text)
        before = (self.read("suggestions.json"), self.read("..", "people", "person-nate.json"))
        old, review.LOCK_WAIT = review.LOCK_WAIT, 0.3
        try:
            with nightly.Lock(os.path.join(self.out, ".lock")):
                rc, text = self.review("accept", "1")
        finally:
            review.LOCK_WAIT = old
        self.assertNotEqual(rc, 0)
        self.assertIn("the nightly run is busy; try again", text)
        after = (self.read("suggestions.json"), self.read("..", "people", "person-nate.json"))
        self.assertEqual(before, after)
        rc, text = self.review("accept", "1")                              # free again
        self.assertEqual(rc, 0, text)

    def test_fallback_lock_recovers_from_a_crashed_run(self):
        lock = os.path.join(self.out, ".lock") + ".run"
        os.makedirs(self.out, exist_ok=True)
        with open(lock, "w") as f:
            f.write("2147483646")                       # a pid that is not running
        fd = nightly.create_run_file(lock)
        os.close(fd)
        with open(lock) as f:
            self.assertEqual(f.read(), str(os.getpid()))
        with self.assertRaises(FileExistsError):        # now held by a live process (this one)
            nightly.create_run_file(lock)
        os.remove(lock)
        with open(lock, "w") as f:
            f.write("garbage")
        old = time.time() - nightly.STALE_LOCK_SECONDS - 60
        os.utime(lock, (old, old))                       # unreadable pid and old: stale
        os.close(nightly.create_run_file(lock))
        os.remove(lock)

    def test_fallback_lock_file_is_honored_without_fcntl(self):
        rc, text = self.nightly()
        self.assertEqual(rc, 0, text)
        before = (self.read("suggestions.json"), self.read("..", "people", "person-nate.json"))
        lock = os.path.join(self.out, ".lock")
        old, review.LOCK_WAIT = review.LOCK_WAIT, 0.3
        real_import = builtins.__import__

        def no_fcntl(name, *a, **k):
            if name == "fcntl":
                raise ImportError("no fcntl")
            return real_import(name, *a, **k)
        try:
            with mock.patch("builtins.__import__", no_fcntl):
                with nightly.Lock(lock):
                    self.assertTrue(os.path.exists(lock + ".run"))
                    rc, text = self.review("accept", "1")
                self.assertNotEqual(rc, 0)
                self.assertIn("the nightly run is busy; try again", text)
                self.assertEqual(before, (self.read("suggestions.json"), self.read("..", "people", "person-nate.json")))
                rc, text = self.review("accept", "1")
                self.assertEqual(rc, 0, text)
                self.assertFalse(os.path.exists(lock + ".run"))
        finally:
            review.LOCK_WAIT = old


class NoCards(Base):
    def test_new_messages_but_no_cards_is_a_failure(self):
        os.remove(self.card_file)
        rc, text = self.nightly()
        self.assertNotEqual(rc, 0, text)
        self.assertIn("no person cards", text)
        self.assertFalse(os.path.exists(os.path.join(self.out, "cursor.json")))


class OwnerSender(Base):
    def setUp(self):
        super().setUp()
        self.text = "I work as a nurse with Nate"
        self.write_export(("m1", self.text, "2026-10-01T10:00:00Z"))
        self.canned = [{"fact": "Works as a nurse.", "quote": self.text, "message_id": "m1"}]

    def test_owner_speaker_is_not_a_fact_on_nates_card_with_owner(self):
        rc, text = self.nightly(extra=["--owner", "Ava"])
        self.assertEqual(rc, 0, text)
        self.assertFalse(os.path.exists(os.path.join(self.out, "facts", "nate.json")))

    def test_owner_speaker_is_not_a_fact_without_any_owner_set(self):
        rc, text = self.nightly()
        self.assertEqual(rc, 0, text)
        self.assertFalse(os.path.exists(os.path.join(self.out, "facts", "nate.json")))


class Owners(Base):
    def setUp(self):
        super().setUp()
        self.owner_text = "Nate says Ona works as a nurse."
        self.write_export(("m1", self.owner_text, "2026-10-01T10:00:00Z"))
        self.canned = [{"fact": "Ona works as a nurse.", "quote": self.owner_text, "message_id": "m1"}]

    def test_no_owner_set_keeps_the_fact(self):
        self.nightly()
        self.assertEqual(len(self.read("facts", "nate.json")), 1)

    def test_owner_from_cli(self):
        rc, text = self.nightly(extra=["--owner", "Ona"])
        self.assertEqual(rc, 0, text)
        self.assertFalse(os.path.exists(os.path.join(self.out, "facts", "nate.json")))
        self.assertIn("1 rejected", text)

    def test_owner_from_env(self):
        rc, text = self.nightly(env={"SCRIPTORIUM_OWNERS": "Pim, Ona"})
        self.assertEqual(rc, 0, text)
        self.assertFalse(os.path.exists(os.path.join(self.out, "facts", "nate.json")))

    def test_old_owners_env_name_is_still_read(self):
        saved = {k: os.environ.pop(k, None) for k in ("SCRIPTORIUM_OWNERS", "MEMORY_ENTRY_OWNERS")}
        try:
            os.environ["MEMORY_ENTRY_OWNERS"] = "Pim"  # the old name
            self.assertEqual(fc.owners_env(), "Pim")
            os.environ["SCRIPTORIUM_OWNERS"] = "Ona"
            self.assertEqual(fc.owners_env(), "Ona")
        finally:
            for k, v in saved.items():
                os.environ.pop(k, None)
                if v is not None:
                    os.environ[k] = v

    def test_owners_from_merges_and_dedupes(self):
        self.assertEqual(fc.owners_from(["Ona", "ona"], "Pim, ,Ona"), ["Ona", "Pim"])
        self.assertEqual(fc.owners_from(None, None), [])

    def test_prompt_names_the_owners(self):
        with open(fc.PROMPT_FILE, encoding="utf-8") as f:
            template = f.read()
        card = {"id": "x", "name": "Nate", "kind": "person"}
        self.assertIn("Ona, Pim", fc.build_prompt(template, card, [], ["Ona", "Pim"]))
        out = fc.build_prompt(template, card, [], [])
        self.assertIn("none named", out)
        self.assertNotIn("{{", out)


class Loading(Base):
    def test_cards_read_in_found_in_chats_shape(self):
        self.write(self.card_file, {"title": "Nate (person card)", "memories": [{"person": "Nate"}],
                                    "links": [{"name": "Jo", "relation": "partner"}],
                                    "fields": {"work": "nurse", "aka": ["Nat"]}})
        c = nightly.load_person_cards(self.cards)[0]
        self.assertEqual((c["id"], c["name"], c["work"], c["aka"]), ("nate", "Nate", "nurse", ["Nat"]))
        self.assertEqual(c["links"], [{"name": "Jo", "relation": "partner"}])

    def test_messages_carry_the_shape(self):
        m = nightly.load_messages(self.export)[0]
        self.assertEqual((m["id"], m["room"], m["thread"], m["said_at"]), ("m1", "chat", "Chat one", "2026-10-01T10:00:00Z"))


class Edges(Base):
    def test_backlog_over_sixty_is_read_in_batches(self):
        msgs = [("b%03d" % i, "Nate mentions thing %d." % i, "2026-10-01T10:%02d:%02dZ" % (i // 60, i % 60))
                for i in range(130)]
        self.write_export(*msgs)
        self.canned = [{"fact": "Nate mentions thing %d." % i, "quote": "Nate mentions thing %d" % i, "message_id": "b%03d" % i}
                       for i in (0, 59, 60, 129)]
        rc, text = self.nightly()
        self.assertEqual(rc, 0, text)
        self.assertEqual(sorted(f["fact"] for f in self.read("facts", "nate.json")),
                         sorted("Nate mentions thing %d." % i for i in (0, 59, 60, 129)))

    def test_same_fact_text_different_quote_and_same_quote_different_fact(self):
        text = "Nate works as a nurse and keeps bees."
        self.write_export(("m1", text, "2026-10-01T10:00:00Z"))
        self.canned = [{"fact": "Works as a nurse.", "quote": text, "message_id": "m1"},
                       {"fact": "Keeps bees.", "quote": text, "message_id": "m1"}]
        self.nightly()
        self.assertEqual([f["fact"] for f in self.read("facts", "nate.json")], ["Works as a nurse.", "Keeps bees."])
        os.remove(os.path.join(self.out, "cursor.json"))
        self.nightly()
        self.assertEqual(len(self.read("facts", "nate.json")), 2)

    def test_message_sharing_the_cursor_time_is_not_skipped(self):
        self.nightly()
        self.write_export(("m1", OLD, "2026-10-01T10:00:00Z"), ("m2", NEW, "2026-10-01T10:00:00Z"))
        rc, text = self.nightly()
        self.assertEqual(rc, 0, text)
        self.assertIn("1 messages read", text)
        self.assertEqual([f["fact"] for f in self.read("facts", "nate.json")], ["Works as a nurse.", "Keeps bees."])
        self.assertEqual(sorted(self.read("cursor.json")["last_ids"]), ["m1", "m2"])
        rc, text = self.nightly()
        self.assertIn("0 new messages", text)

    def test_two_word_owner_does_not_reject_facts_starting_with_one_of_its_words(self):
        text = "Nate says Mia Voss keeps bees and Mia Voss said hi."
        self.write_export(("m1", text, "2026-10-01T10:00:00Z"))
        cases = [("Mia keeps bees.", "kept"), ("Mia Voss keeps bees.", "rejected")]
        for fact, want in cases:
            out = os.path.join(self.tmp, "o-" + want)
            self.canned = [{"fact": fact, "quote": "Mia Voss keeps bees", "message_id": "m1"}]
            self.out = out
            self.nightly(extra=["--owner", "Mia Voss"])
            self.assertEqual(os.path.exists(os.path.join(out, "facts", "nate.json")), want == "kept", fact)


class OwnerSentinel(Base):
    def test_owner_messages_are_not_grouped_onto_a_card_that_carries_the_owners_name(self):
        m = nightly.load_messages(self.export)[0]
        self.assertEqual(m["sender"], "owner")
        cards = [{"id": "bob", "name": "Bob", "aka": ["Ava", "Owner"]}]
        self.assertEqual(fc.group_messages(cards, [dict(m, text="nothing here")], ["Ava"]), {})

    def test_load_messages_never_rewrites_the_speaker_to_an_owner_name(self):
        self.assertEqual(nightly.load_messages(self.export)[0]["sender"], "owner")


class AcceptedFieldsSurvive(Base):
    def test_accepted_value_survives_a_rewritten_card_and_is_not_proposed_again(self):
        self.nightly()
        self.assertEqual(self.review("accept", "1")[0], 0)
        self.assertTrue(os.path.isfile(os.path.join(self.out, "fields", "nate.json")))
        self.write(self.card_file, {"title": "Nate (person card)", "memories": [{"person": "Nate"}]})  # as write_people would
        self.assertEqual(nightly.load_person_cards(self.cards, self.out)[0]["place"], "Lisbon")
        os.remove(os.path.join(self.out, "cursor.json"))
        self.nightly()
        sugs = self.read("suggestions.json")
        self.assertEqual([(s["field"], s["status"]) for s in sugs], [("place", "accepted")])


class Undated(Base):
    def test_undated_messages_are_reported_and_hold_the_cursor(self):
        self.write_export(("m1", OLD, "2026-10-01T10:00:00Z"), ("m2", NEW, "not a time"))
        rc, text = self.nightly()
        self.assertNotEqual(rc, 0)
        self.assertIn("1 messages had no time and were not read", text)
        self.assertFalse(os.path.exists(os.path.join(self.out, "cursor.json")))

    def test_only_undated_messages_still_fail(self):
        self.write_export(("m1", OLD, "garbage"))
        rc, text = self.nightly()
        self.assertNotEqual(rc, 0)
        self.assertIn("had no time", text)


class DayRule(Base):
    def setUp(self):
        super().setUp()
        self.write_export(("m1", OLD, "2026-10-20T20:00:00Z"))

    def test_no_zone_uses_the_timestamps_own_date(self):
        self.nightly()
        self.assertEqual(self.read("facts", "nate.json")[0]["said_on"], "2026-10-20")

    def test_a_zone_converts_first(self):
        self.nightly(extra=["--timezone", "Asia/Jakarta"])
        self.assertEqual(self.read("facts", "nate.json")[0]["said_on"], "2026-10-21")
        self.assertIsNone(os.environ.get("MEMORY_WRITER_TZ"))
        os.remove(os.path.join(self.out, "cursor.json"))
        self.nightly()          # a later run without a zone is not left on the old one
        self.assertEqual(fc.day_of("2026-10-20T20:00:00Z"), "2026-10-20")


class RemoveFact(Base):
    def test_expect_refuses_a_fact_that_moved(self):
        self.canned = [{"fact": "Works as a nurse.", "quote": "Nate works as a nurse", "message_id": "m1"},
                       {"fact": "Lives in Lisbon.", "quote": "He lives in Lisbon", "message_id": "m1"}]
        self.nightly()
        facts = self.read("facts", "nate.json")
        fp_lisbon = review.fact_fp(facts[1])
        self.assertEqual(self.review("remove-fact", "nate", "1")[0], 0)          # the nurse fact goes; Lisbon is now number 1
        rc, text = self.review("--expect", fp_lisbon, "remove-fact", "nate", "2")  # a stale tab still says number 2
        self.assertNotEqual(rc, 0)
        self.assertEqual(self.review("--expect", "000000000000", "edit-fact", "nate", "1", "Changed.")[0], 1)
        self.assertEqual([f["fact"] for f in self.read("facts", "nate.json")], ["Lives in Lisbon."])
        self.assertEqual(self.review("--expect", fp_lisbon, "edit-fact", "nate", "1", "Lives in Porto.")[0], 0)
        self.assertEqual(self.read("facts", "nate.json")[0]["fact"], "Lives in Porto.")
        self.assertEqual(self.review("--expect", review.fact_fp(self.read("facts", "nate.json")[0]), "remove-fact", "nate", "1")[0], 0)
        self.assertEqual(self.read("facts", "nate.json"), [])

    def test_remove_fact_deletes_the_nth_fact(self):
        self.canned = [{"fact": "Works as a nurse.", "quote": "Nate works as a nurse", "message_id": "m1"},
                       {"fact": "Lives in Lisbon.", "quote": "He lives in Lisbon", "message_id": "m1"}]
        self.nightly()
        self.assertEqual(len(self.read("facts", "nate.json")), 2)
        rc, text = self.review("remove-fact", "nate", "1")
        self.assertEqual(rc, 0, text)
        self.assertEqual([f["fact"] for f in self.read("facts", "nate.json")], ["Lives in Lisbon."])
        self.assertNotEqual(self.review("remove-fact", "nate", "5")[0], 0)
        self.assertNotEqual(self.review("remove-fact", "../x", "1")[0], 0)

    def test_remove_fact_takes_the_lock(self):
        self.nightly()
        with nightly.Lock(os.path.join(self.out, ".lock")), mock.patch.object(review, "LOCK_WAIT", 0.3):
            rc, text = self.review("remove-fact", "nate", "1")
        self.assertNotEqual(rc, 0)
        self.assertIn("busy", text)
        self.assertEqual(len(self.read("facts", "nate.json")), 1)

    def test_edit_fact_replaces_the_text_and_marks_it_edited(self):
        self.canned = [{"fact": "Works as a nurse.", "quote": "Nate works as a nurse", "message_id": "m1"}]
        self.nightly()
        rc, text = self.review("edit-fact", "nate", "1", "Works nights as a nurse.")
        self.assertEqual(rc, 0, text)
        f = self.read("facts", "nate.json")[0]
        self.assertEqual((f["fact"], f["status"], f["quote"]), ("Works nights as a nurse.", "edited", "Nate works as a nurse"))
        self.assertNotEqual(self.review("edit-fact", "nate", "4", "x")[0], 0)
        self.assertNotEqual(self.review("edit-fact", "nate", "1", "   ")[0], 0)
        self.assertNotEqual(self.review("edit-fact", "../x", "1", "x")[0], 0)


if __name__ == "__main__":
    unittest.main()
