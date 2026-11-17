# Scriptorium — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""Review of the facts the nightly run set aside as "check by hand" (review.py held, keep-held, drop-held)."""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "engine"))
import review  # noqa: E402

ITEM = {"person_id": "nate", "name": "Nate", "fact": "Plays the cello.", "field": None, "value": None,
        "quote": "Nate plays the cello", "message_id": "m1", "why": "the quoted sentence is negative or past; check by hand"}


def line(item=ITEM, source="engine", reason="x; check by hand", at="2026-11-01T02:00:00Z"):
    return json.dumps({"at": at, "source": source, "reason": reason, "item": item})


class Held(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.out = self._t.name

    def rejected(self, *lines):
        with open(os.path.join(self.out, "rejected.jsonl"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

    def run_review(self, *args):
        buf, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            rc = review.main(["--out", self.out, *args])
        return rc, buf.getvalue() + err.getvalue()

    def decided(self):
        with open(os.path.join(self.out, "held_decided.json"), encoding="utf-8") as f:
            return json.load(f)

    def held(self):
        rc, text = self.run_review("held")
        self.assertEqual(rc, 0, text)
        return json.loads(text)

    def test_lists_only_engine_check_by_hand_facts(self):
        self.rejected(line(), line(dict(ITEM, fact=None, field="place"), reason="conflicting; check by hand"),
                      line(dict(ITEM, fact="Other."), source="sink"), line(dict(ITEM, fact="Else."), reason="too long"),
                      line(dict(ITEM, fact="  "), reason="check by hand"), "not json", line(dict(ITEM, person_id="", fact="No one.")))
        got = self.held()
        self.assertEqual([h["fact"] for h in got], ["Plays the cello."])
        h = got[0]
        self.assertEqual((h["person_id"], h["person_name"], h["quote"], h["message_id"], h["at"]),
                         ("nate", "Nate", "Nate plays the cello", "m1", "2026-11-01T02:00:00Z"))
        self.assertRegex(h["id"], r"^[0-9a-f]{12}$")

    def test_id_is_stable_and_duplicates_collapse(self):
        self.rejected(line(), line(at="2026-11-02T02:00:00Z"))
        a = self.held()
        self.assertEqual(len(a), 1)
        self.assertEqual(a[0]["id"], review.held_id(ITEM))
        self.assertNotEqual(review.held_id(ITEM), review.held_id(dict(ITEM, message_id="m2")))

    def test_keep_writes_the_fact_once_and_is_remembered(self):
        self.rejected(line())
        hid = self.held()[0]["id"]
        rc, text = self.run_review("keep-held", hid)
        self.assertEqual(rc, 0, text)
        with open(os.path.join(self.out, "facts", "nate.json"), encoding="utf-8") as f:
            facts = json.load(f)
        self.assertEqual(facts, [{"fact": "Plays the cello.", "quote": "Nate plays the cello", "source": "chat",
                                  "message_ids": ["m1"]}])      # no said_on: the set-aside time is not when it was said
        self.assertEqual(self.held(), [])
        self.assertEqual(self.decided()[hid], "kept")
        self.assertNotEqual(self.run_review("keep-held", hid)[0], 0)      # not twice

    def test_keep_does_not_duplicate_a_fact_already_there(self):
        os.makedirs(os.path.join(self.out, "facts"))
        with open(os.path.join(self.out, "facts", "nate.json"), "w", encoding="utf-8") as f:
            json.dump([{"fact": "plays the  cello.", "quote": "Nate plays the cello", "person_id": "nate"}], f)
        self.rejected(line())
        self.assertEqual(self.run_review("keep-held", self.held()[0]["id"])[0], 0)
        with open(os.path.join(self.out, "facts", "nate.json"), encoding="utf-8") as f:
            self.assertEqual(len(json.load(f)), 1)
        self.assertEqual(self.held(), [])

    def test_drop_is_remembered_and_writes_no_fact(self):
        self.rejected(line())
        hid = self.held()[0]["id"]
        self.assertEqual(self.run_review("drop-held", hid)[0], 0)
        self.assertEqual(self.held(), [])
        self.assertFalse(os.path.exists(os.path.join(self.out, "facts")))
        self.assertEqual(self.decided()[hid], "dropped")

    def test_unknown_id_fails(self):
        self.rejected(line())
        rc, text = self.run_review("keep-held", "000000000000")
        self.assertNotEqual(rc, 0)
        self.assertIn("no set-aside fact", text)

    def test_unsafe_key_is_refused_and_nothing_is_decided(self):
        bad = dict(ITEM, person_id="../evil")
        self.rejected(line(bad))
        rc, text = self.run_review("keep-held", review.held_id(bad))
        self.assertNotEqual(rc, 0)
        self.assertFalse(os.path.exists(os.path.join(self.out, "held_decided.json")))
        self.assertFalse(os.path.exists(os.path.join(self.out, "..", "evil.json")))


if __name__ == "__main__":
    unittest.main()
