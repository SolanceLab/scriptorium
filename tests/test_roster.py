# Scriptorium — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""The roster runner with a fake model, a fake source and a fake sink, and invented people.
Run: python3 -m unittest test_roster -v"""
import contextlib
import datetime as dt
import io
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "engine"))
import found_in_chats as fc  # noqa: E402
import nightly  # noqa: E402
import roster as rr  # noqa: E402

# The fake model answers with every canned item whose message id is in the prompt. A message id starting with "z"
# makes it fail, so one person can fail while the others work.
FAKE = '''import json, re, sys
prompt = sys.stdin.read()
ids = set(re.findall(r"^\\[([^\\]]+)\\] ", prompt, re.M))
if any(i.startswith("z") for i in ids):
    sys.exit(3)
canned = json.load(open(%r))
print(json.dumps({"facts": [c for c in canned if c["message_id"] in ids]}))
'''

T0 = dt.datetime(2026, 11, 1, 12, 0, 0, tzinfo=dt.timezone.utc)
NOW = T0 + dt.timedelta(hours=2)


def at(minutes):
    return (T0 + dt.timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


HOBBIES = {"m1": "bees", "m2": "chess", "m3": "tennis", "m4": "sailing"}


def msg(mid, minutes, text=None, sender="Ivy"):
    return {"id": mid, "sender": sender, "said_at": at(minutes), "room": "chat", "thread": "t",
            "text": text or "Nate plays %s." % HOBBIES.get(mid, "bees")}


class FakeSource:
    def __init__(self, messages, cards):
        self.messages, self._cards, self.seen_cursors = messages, cards, []

    def read(self, cursor):
        self.seen_cursors.append(dict(cursor))
        last, ids = cursor.get("last_said_at"), set(cursor.get("last_ids") or [])
        return [m for m in self.messages
                if not last or m["said_at"] > last or (m["said_at"] == last and m["id"] not in ids)]

    def cards(self):
        return self._cards


class FakeSink:
    """Keeps what it is given; a second write of the same item is skipped (the way a real store dedups)."""

    def __init__(self, answer=None, crash_after=None):
        self.store, self.calls, self.answer, self.crash_after = {}, 0, answer, crash_after

    def write(self, facts, suggestions):
        self.calls += 1
        stored = skipped = 0
        for n, e in enumerate(list(facts) + list(suggestions)):
            if self.crash_after is not None and n >= self.crash_after:
                raise RuntimeError("connection lost")
            key = json.dumps([e.get("person_id"), e.get("quote"), e.get("field")], sort_keys=True)
            if key in self.store:
                skipped += 1
            else:
                self.store[key] = e
                stored += 1
        if self.answer is not None:
            return self.answer
        return {"stored": stored, "skipped": skipped, "rejected": [], "deferred": 0}


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.addCleanup(self._tmp.cleanup)
        self.state = os.path.join(self.tmp, "state")
        self.cards = [{"id": "nate", "name": "Nate", "kind": "person", "aka": [], "links": []}]
        self.canned = [{"fact": "Plays %s." % h, "quote": "Nate plays %s" % h, "message_id": mid}
                       for mid, h in HOBBIES.items()]

    def model(self, body=None):
        canned = os.path.join(self.tmp, "canned.json")
        with open(canned, "w") as f:
            json.dump(self.canned, f)
        path = os.path.join(self.tmp, "fake.py")
        with open(path, "w") as f:
            f.write(body if body is not None else FAKE % canned)
        return "%s %s" % (sys.executable, path)

    def run_it(self, source, sink, **kw):
        kw.setdefault("now", NOW)
        kw.setdefault("log", lambda *a: None)
        return rr.run_roster(source, sink, self.state, model_cmd=self.model(kw.pop("body", None)), **kw)

    def read(self, name):
        with open(os.path.join(self.state, name), encoding="utf-8") as f:
            return json.load(f)

    def rejected_lines(self):
        path = os.path.join(self.state, "rejected.jsonl")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def has(self, name):
        return os.path.exists(os.path.join(self.state, name))


class Happy(Base):
    def test_cursor_advances_and_status_is_written(self):
        sink = FakeSink()
        st = self.run_it(FakeSource([msg("m1", 0), msg("m2", 5)], self.cards), sink)
        self.assertTrue(st["ok"], st)
        self.assertEqual((st["messages"], st["people_seen"], st["stored"], st["skipped"]), (2, 1, 2, 0))
        cur = self.read("cursor.json")
        self.assertEqual((cur["last_said_at"], cur["last_id"]), (at(5), "m2"))
        disk = self.read("status.json")
        for k in ("ok", "at", "messages", "people_seen", "facts_stored", "suggestions_stored", "skipped",
                  "rejected_by_reason", "notes", "error"):
            self.assertIn(k, disk)
        self.assertTrue(disk["ok"])
        self.assertIsNone(disk["error"])

    def test_second_run_reads_only_what_is_newer(self):
        src, sink = FakeSource([msg("m1", 0)], self.cards), FakeSink()
        self.run_it(src, sink)
        src.messages.append(msg("m2", 5))
        st = self.run_it(src, sink)
        self.assertEqual((st["messages"], st["stored"]), (1, 1))
        self.assertEqual(src.seen_cursors[1]["last_said_at"], at(0))
        self.assertEqual(self.read("cursor.json")["last_said_at"], at(5))

    def test_a_run_with_nothing_new_still_writes_status(self):
        st = self.run_it(FakeSource([], self.cards), FakeSink())
        self.assertTrue(st["ok"])
        self.assertTrue(self.read("status.json")["ok"])
        self.assertFalse(self.has("cursor.json"))

    def test_no_cards_fails_and_holds_the_cursor(self):
        st = self.run_it(FakeSource([msg("m1", 0)], []), FakeSink())
        self.assertFalse(st["ok"])
        self.assertFalse(self.has("cursor.json"))


class Settle(Base):
    def test_a_message_exactly_at_the_boundary_is_read_and_one_just_inside_waits(self):
        now = T0 + dt.timedelta(minutes=20)
        src = FakeSource([msg("m1", 5), msg("m2", 10), msg("m3", 10, sender="Ivy")], self.cards)
        src.messages[2]["said_at"] = (T0 + dt.timedelta(minutes=10, seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        st = self.run_it(src, FakeSink(), now=now, settle_minutes=10)
        self.assertTrue(st["ok"], st)
        self.assertEqual(st["messages"], 2)                       # m1 and m2 (exactly 10 minutes old)
        self.assertEqual(st["waiting_to_settle"], 1)              # m3 is 9 min 59 s old
        self.assertEqual(self.read("cursor.json")["last_said_at"], at(10))
        st = self.run_it(src, FakeSink(), now=now + dt.timedelta(minutes=5), settle_minutes=10)
        self.assertEqual((st["messages"], st["waiting_to_settle"]), (1, 0))   # eligible later, not lost

    def test_zero_settle_reads_everything(self):
        st = self.run_it(FakeSource([msg("m1", 119)], self.cards), FakeSink(), settle_minutes=0)
        self.assertEqual(st["messages"], 1)


class Cap(Base):
    def test_the_rest_waits_for_the_next_run_and_the_cursor_stops_before_it(self):
        src, sink = FakeSource([msg("m1", 0), msg("m2", 5), msg("m3", 10), msg("m4", 15)], self.cards), FakeSink()
        st = self.run_it(src, sink, per_person_cap=2)
        self.assertTrue(st["ok"], st)
        self.assertEqual(st["held"], 2)
        self.assertTrue(any("wait for the next run" in n for n in st["notes"]), st["notes"])
        self.assertEqual(len(sink.store), 2)
        self.assertEqual(self.read("cursor.json")["last_said_at"], at(5))
        st = self.run_it(src, sink, per_person_cap=2)
        self.assertEqual((st["held"], len(sink.store)), (0, 4))   # nothing dropped
        self.assertEqual(self.read("cursor.json")["last_said_at"], at(15))

    def test_no_cap_reads_all(self):
        src = FakeSource([msg("m1", 0), msg("m2", 5), msg("m3", 10)], self.cards)
        st = self.run_it(src, FakeSink(), per_person_cap=None)
        self.assertEqual((st["held"], st["stored"]), (0, 3))

    def test_a_cap_below_one_is_refused(self):
        with self.assertRaises(rr.RosterError):
            self.run_it(FakeSource([], self.cards), FakeSink(), per_person_cap=0)


class Lock(Base):
    def test_a_second_run_exits_locked_and_changes_nothing(self):
        os.makedirs(self.state)
        sink = FakeSink()
        with rr.Lock(os.path.join(self.state, "roster.lock")):
            st = self.run_it(FakeSource([msg("m1", 0)], self.cards), sink)
        self.assertEqual((st["state"], st["ok"]), ("locked", False))
        self.assertEqual(sink.calls, 0)
        self.assertFalse(self.has("status.json"))
        self.assertFalse(self.has("cursor.json"))
        self.assertTrue(self.run_it(FakeSource([msg("m1", 0)], self.cards), sink)["ok"])   # free afterwards


class HoldsTheCursor(Base):
    def setUp(self):
        super().setUp()
        self.cards.append({"id": "zed", "name": "Zed", "kind": "person", "aka": [], "links": []})
        self.canned.append({"fact": "Plays golf.", "quote": "Zed plays golf", "message_id": "z1"})

    def test_an_engine_error_for_one_person_holds_the_cursor(self):
        src = FakeSource([msg("m1", 0), msg("z1", 5, "Zed plays golf.")], self.cards)
        sink = FakeSink()
        st = self.run_it(src, sink)
        self.assertFalse(st["ok"])
        self.assertEqual(st["reason"], "engine_errors")
        self.assertEqual(len(st["errors"]), 1)
        self.assertEqual(len(sink.store), 1)                      # the other person's fact was still written
        self.assertFalse(self.has("cursor.json"))
        self.assertFalse(self.read("status.json")["ok"])

    def test_deferred_items_hold_the_cursor(self):
        sink = FakeSink(answer={"stored": 0, "skipped": 0, "rejected": [], "deferred": 1})
        st = self.run_it(FakeSource([msg("m1", 0)], self.cards), sink)
        self.assertFalse(st["ok"])
        self.assertEqual(st["reason"], "deferred")
        self.assertFalse(self.has("cursor.json"))

    def test_a_malformed_sink_answer_fails_the_run_and_holds_the_cursor(self):
        class Silent(FakeSink):
            def write(self, facts, suggestions):
                return None
        st = self.run_it(FakeSource([msg("m1", 0)], self.cards), Silent())
        self.assertEqual((st["ok"], st["reason"]), (False, "sink_malformed"))
        for bad in ("ok", {"stored": 1}, {"stored": "1", "skipped": 0, "rejected": [], "deferred": 0},
                    {"stored": 1, "skipped": 0, "rejected": ["x"], "deferred": 0},
                    {"stored": 1, "skipped": 0, "rejected": [], "deferred": True}):
            st = self.run_it(FakeSource([msg("m1", 0)], self.cards), FakeSink(answer=bad))
            self.assertFalse(st["ok"], bad)
            self.assertEqual(st["reason"], "sink_malformed", bad)
            self.assertFalse(self.has("cursor.json"), bad)

    def test_an_answer_whose_counts_do_not_add_up_to_the_items_sent_holds_the_cursor(self):
        zero = {"stored": 0, "skipped": 0, "rejected": [], "deferred": 0}
        st = self.run_it(FakeSource([msg("m1", 0)], self.cards), FakeSink(answer=zero))
        self.assertEqual((st["ok"], st["reason"]), (False, "sink_malformed"))
        self.assertFalse(self.has("cursor.json"))
        with self.assertRaises(rr.RosterError):
            rr.check_answer(zero, 2)
        self.assertEqual(rr.check_answer(zero, 0), zero)

    def test_a_sink_that_raises_fails_the_run(self):
        st = self.run_it(FakeSource([msg("m1", 0)], self.cards), FakeSink(crash_after=0))
        self.assertFalse(st["ok"])
        self.assertIn("connection lost", st["error"])
        self.assertFalse(self.has("cursor.json"))
        self.assertTrue(self.has("status.json"))

    def test_partial_post_then_crash_then_replay_loses_nothing_and_repeats_nothing(self):
        src = FakeSource([msg("m1", 0), msg("m2", 5), msg("m3", 10)], self.cards)
        sink = FakeSink(crash_after=2)
        self.assertFalse(self.run_it(src, sink)["ok"])
        self.assertEqual(len(sink.store), 2)
        sink.crash_after = None
        st = self.run_it(src, sink)
        self.assertTrue(st["ok"], st)
        self.assertEqual((st["stored"], st["skipped"]), (1, 2))   # the two already posted are counted as skipped
        self.assertEqual(len(sink.store), 3)
        self.assertEqual(self.read("cursor.json")["last_said_at"], at(10))

    def test_a_message_without_a_time_holds_the_cursor(self):
        bad = msg("m2", 5)
        bad["said_at"] = "sometime"
        src = FakeSource([msg("m1", 0), bad], self.cards)
        src.read = lambda cursor: src.messages
        st = self.run_it(src, FakeSink())
        self.assertFalse(st["ok"])
        self.assertEqual(st["undated"], 1)
        self.assertFalse(self.has("cursor.json"))


class Quarantine(Base):
    def test_engine_and_sink_rejects_both_land_in_rejected_jsonl_and_do_not_hold_the_cursor(self):
        self.canned.append({"fact": "Plays polo.", "quote": "Nate plays polo", "message_id": "m1"})   # not in the message
        sink = FakeSink(answer={"stored": 1, "skipped": 0, "deferred": 0,
                                "rejected": [{"item": {"person_id": "nate", "fact": "Plays chess."},
                                              "reason": "the store refused it"}]})
        st = self.run_it(FakeSource([msg("m1", 0), msg("m2", 5)], self.cards), sink)
        self.assertTrue(st["ok"], st)
        lines = self.rejected_lines()
        self.assertEqual(sorted(l["source"] for l in lines), ["engine", "sink"])
        engine = next(l for l in lines if l["source"] == "engine")
        self.assertTrue(engine["reason"])
        self.assertEqual(engine["item"]["quote"], "Nate plays polo")
        self.assertEqual(next(l for l in lines if l["source"] == "sink")["reason"], "the store refused it")
        self.assertEqual(st["rejected"], 2)
        self.assertEqual(sum(st["rejected_by_reason"].values()), 2)
        self.assertIn("the store refused it", st["rejected_by_reason"])
        self.assertEqual(self.read("cursor.json")["last_said_at"], at(5))

    def test_a_retried_run_does_not_repeat_its_rejected_lines(self):
        self.canned.append({"fact": "Plays polo.", "quote": "Nate plays polo", "message_id": "m1"})   # not in the message
        src = FakeSource([msg("m1", 0)], self.cards)
        self.run_it(src, FakeSink(answer={"stored": 0, "skipped": 0, "rejected": [], "deferred": 1}))  # held: retried
        self.run_it(src, FakeSink())
        self.assertEqual(len(self.rejected_lines()), 1)

    def test_rejected_lines_are_appended_across_runs(self):
        self.canned.append({"fact": "Plays polo.", "quote": "Nate plays polo", "message_id": "m1"})
        src, sink = FakeSource([msg("m1", 0)], self.cards), FakeSink()
        self.run_it(src, sink)
        src.messages.append(msg("m2", 5))
        self.canned.append({"fact": "Plays golf.", "quote": "Nate plays golf", "message_id": "m2"})
        self.run_it(src, sink)
        self.assertEqual(len(self.rejected_lines()), 2)


class Plugging(Base):
    def setUp(self):
        super().setUp()
        self.mod_dir = os.path.join(self.tmp, "mods")
        os.makedirs(self.mod_dir)
        with open(os.path.join(self.mod_dir, "my_store_for_test.py"), "w") as f:
            f.write("class Src:\n"
                    "    def read(self, cursor):\n"
                    "        return [{'id': 'm1', 'sender': 'Ivy', 'said_at': '2026-11-01T12:00:00Z', 'text': 'Nate plays bees.'}]\n"
                    "    def cards(self):\n"
                    "        return [{'id': 'nate', 'name': 'Nate', 'kind': 'person', 'aka': [], 'links': []}]\n"
                    "class Snk:\n"
                    "    got = []\n"
                    "    def write(self, facts, suggestions):\n"
                    "        Snk.got = facts\n"
                    "        return {'stored': len(facts), 'skipped': 0, 'rejected': [], 'deferred': 0}\n"
                    "instance = Src()\n"
                    "not_a_source = 5\n")
        sys.path.insert(0, self.mod_dir)
        self.addCleanup(lambda: (sys.path.remove(self.mod_dir), sys.modules.pop("my_store_for_test", None)))

    def test_module_attr_loads_an_instance_or_calls_a_class(self):
        self.assertTrue(hasattr(rr.load_ref("my_store_for_test:instance", ("read", "cards")), "read"))
        self.assertTrue(hasattr(rr.load_ref("my_store_for_test:Src", ("read", "cards")), "cards"))
        self.assertTrue(hasattr(rr.load_ref("my_store_for_test:Snk", ("write",)), "write"))

    def test_bad_references_are_named(self):
        for ref in ("nomodule", "no_such_module_here:x", "my_store_for_test:missing", "my_store_for_test:not_a_source",
                    "my_store_for_test:Snk"):
            with self.assertRaises(rr.RosterError, msg=ref):
                rr.load_ref(ref, ("read", "cards"))

    def test_nightly_runs_with_your_own_source_and_sink(self):
        out = os.path.join(self.tmp, "found")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            rc = nightly.main(["--out", out, "--source", "my_store_for_test:Src", "--sink", "my_store_for_test:Snk",
                               "--model-cmd", self.model()])
        self.assertEqual(rc, 0, buf.getvalue())
        sink_mod = sys.modules["my_store_for_test"]
        self.assertEqual([f["message_ids"] for f in sink_mod.Snk.got], [["m1"]])
        self.assertTrue(os.path.exists(os.path.join(out, "status.json")))
        self.assertFalse(os.path.exists(os.path.join(out, "facts")))      # the default sink was not used

    def test_nightly_needs_export_and_cards_without_a_source(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit):
            nightly.main(["--out", os.path.join(self.tmp, "found")])


if __name__ == "__main__":
    unittest.main()
