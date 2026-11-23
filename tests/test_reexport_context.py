# Scriptorium — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""Re-export: earlier notes and the last 10 turns reach the writer as context only; only new lines are citable."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine"))
sys.path.insert(0, os.path.dirname(__file__))
import import_captures as ic  # noqa: E402
import write_notes as wn  # noqa: E402
from test_capture_import import Base, Fake, capture, msg  # noqa: E402


def turns(n, start=0):
    out = []
    for i in range(start, start + n):
        out += [msg("user", f"question number {i} here", f"u{i}"), msg("assistant", f"answer number {i} here", f"a{i}")]
    return out


class Noting(Fake):
    def __call__(self, conv):
        res = super().__call__(conv)
        for m in res["memories"]:
            m.update(topic="Plans", summary="note about " + m["excerpt"])
        return res


class ContextTest(Base):
    def setUp(self):
        super().setUp()
        self.fake = Noting()

    def test_first_export_has_no_context(self):
        self.put(capture(messages=turns(3)))
        self.run_import()
        self.assertNotIn("context", self.fake.calls[0])

    def test_second_export_sends_notes_and_last_10_turns_only(self):
        self.put(capture(messages=turns(14)))
        self.run_import()
        self.put(capture(messages=turns(16)))
        self.run_import()
        conv = self.fake.calls[1]
        self.assertEqual([m["uuid"] for m in conv["chat_messages"]], ["u14", "a14", "u15", "a15"])
        ctx = conv["context"]
        self.assertEqual(len(ctx["notes"]), 28)
        self.assertEqual(len(ctx["turns"]), 20)
        self.assertEqual(ctx["turns"][0], ("user", "question number 4 here"))
        self.assertEqual(ctx["turns"][-1], ("assistant", "answer number 13 here"))
        self.assertEqual(ctx["left_out"], 0)   # only the size cap counts as left out

    def test_cap_keeps_most_recent_and_says_so(self):
        self.put(capture(messages=turns(5)))
        self.run_import()
        self.put(capture(messages=turns(6)))
        ic.import_root(self.root, self.out, self.fake, context_chars=100)
        ctx = self.fake.calls[1]["context"]
        self.assertLess(len(ctx["turns"]), 10)
        self.assertEqual(ctx["turns"][-1], ("assistant", "answer number 4 here"))
        self.assertEqual(ctx["left_out"], 5 - len(ctx["turns"]) // 2)

    def test_prompt_marks_context_without_ids_and_citing_it_is_rejected(self):
        ctx = {"notes": ["Plans: we booked a trip"], "turns": [("user", "old question words")], "left_out": 2}
        prompts = []

        def fetch(prompt, timeout):
            prompts.append(prompt)
            thread = lambda lid, ex: {"topic": "Plans", "excerpt": ex, "summary": "s", "source_line_ids": [lid],
                                      "source_session_date": "2026-11-23"}
            return json.dumps([thread("old1", "old question words"), thread("new1", "fresh message text")]), {}

        conv = {"uuid": "c", "name": "T", "created_at": "2026-11-23T10:00:00Z", "context": ctx,
                "chat_messages": [{"uuid": "new1", "sender": "human", "text": "fresh message text"}]}
        res = wn.write_conversation(conv, "TEMPLATE", "x", 5, api_call=fetch, builtin_prompt=True)
        p = prompts[0]
        self.assertIn("context only, do not write notes about it, do not cite it", p)
        self.assertIn("Plans: we booked a trip", p)
        self.assertIn("2 earlier turn(s) left out", p)
        self.assertIn("[new1] user: fresh message text", p)
        self.assertNotIn("[old1]", p)
        self.assertEqual(len(res["memories"]), 1)
        self.assertEqual(len(res["rejected"]), 1)

    def test_no_context_prompt_unchanged(self):
        lines = [("l1", "user", "hi there", None)]
        self.assertNotIn("Earlier in this conversation", wn.build_prompt("T", "t", "2026-11-23", lines))


if __name__ == "__main__":
    unittest.main()
