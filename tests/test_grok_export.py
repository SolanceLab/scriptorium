# Scriptorium — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""Grok's account export through the import path, with invented chats and a fake writer.
Run: python3 -m unittest test_grok_export -v"""
import builtins
import json
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "engine"))
import grok_export as grok  # noqa: E402
from test_capture_import import Base, capture, msg  # noqa: E402


def resp(rid, parent, sender, text, at=1700000000000):
    t = {"$date": {"$numberLong": str(at)}} if isinstance(at, int) else at
    return {"response": {"_id": rid, "conversation_id": "k1", "message": text, "sender": sender,
                         "create_time": t, "parent_response_id": parent}, "share_link": None}


def chat(cid="k1", leaf="r3", extra=(), temporary=False):
    c = {"id": cid, "title": "Invented", "leaf_response_id": leaf, "temporary": temporary}
    return {"conversation": c, "responses": [resp("r1", None, "human", "first question"),
                                             resp("r2", "r1", "ASSISTANT", "first answer", 1700000001000),
                                             resp("r3", "r2", "human", "second question", 1700000002000), *extra]}


class Reading(Base):
    def put_export(self, items, folder="", extra_files=None):
        d = os.path.join(self.root, folder)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, grok.BACKEND), "w") as f:
            json.dump({"conversations": items, "projects": [], "tasks": []}, f)
        for name, data in (extra_files or {}).items():
            with open(os.path.join(d, name), "w") as f:
                f.write(data)

    def texts(self, item):
        return [(m["role"], m["text"]) for m in grok.conversation_turns(item)[2]]

    def test_linear_chat_in_order_with_mixed_senders(self):
        item = chat(extra=[resp("r4", "r3", "grok-4-auto", "third"), resp("r5", "r4", "assistant", "fourth")], leaf="r5")
        self.assertEqual(self.texts(item), [("user", "first question"), ("assistant", "first answer"),
                                            ("user", "second question"), ("assistant", "third"), ("assistant", "fourth")])

    def test_branch_follows_leaf_only(self):
        extra = [resp("r3b", "r2", "human", "the unedited question")]
        self.assertEqual(self.texts(chat(extra=extra))[-1], ("user", "second question"))
        self.assertEqual(self.texts(chat(leaf="r3b", extra=extra))[-1], ("user", "the unedited question"))

    def test_missing_leaf_uses_latest_message_chain(self):
        extra = [resp("r3b", "r2", "human", "newest", 1700000009000)]
        self.assertEqual(self.texts(chat(leaf=None, extra=extra))[-1], ("user", "newest"))

    def test_timestamps_long_iso_and_number(self):
        item = chat(extra=[resp("r4", "r3", "human", "iso", "2023-11-14T22:13:20Z"), resp("r5", "r4", "human", "num", 1700000000)],
                    leaf="r5")
        item["responses"][-1]["response"]["create_time"] = 1700000000.0
        got = [m["created_at"] for m in grok.conversation_turns(item)[2]]
        self.assertEqual(got[0], "2023-11-14T22:13:20Z")
        self.assertEqual(got[3], "2023-11-14T22:13:20Z")
        self.assertEqual(got[4], "2023-11-14T22:13:20Z")

    def test_empty_messages_skipped_and_broken_trees_do_not_crash(self):
        item = chat(extra=[resp("r4", "r3", "assistant", "  ")], leaf="r4")
        self.assertEqual(len(self.texts(item)), 3)
        loop = {"conversation": {"id": "x", "leaf_response_id": "a"},
                "responses": [resp("a", "b", "human", "x"), resp("b", "a", "human", "y")]}
        self.assertEqual(len(grok.conversation_turns(loop)[2]), 2)

    def test_temporary_chat_skipped_and_nested_folder_found(self):
        self.put_export([chat("keep"), chat("gone", temporary=True)], folder=os.path.join("1d", "export_data", "uuid"))
        self.assertEqual(self.run_import()["written"], 3)
        self.assertEqual(len(self.fake.calls), 1)
        self.assertEqual(self.notes("grok", "keep")["title"], "Invented")
        self.assertFalse(os.path.exists(os.path.join(self.out, "grok-gone.json")))

    def test_auth_and_billing_files_are_never_opened(self):
        self.put_export([chat()], extra_files={"prod-mc-auth-mgmt-api.json": "{not json", "prod-mc-billing.json": "{not json"})
        real, opened = builtins.open, []

        def spy(path, *a, **k):
            opened.append(str(path))
            return real(path, *a, **k)
        with mock.patch("builtins.open", spy):
            rep = self.run_import()
        self.assertEqual((rep["written"], rep["error"]), (3, []))
        self.assertFalse([p for p in opened if "auth" in p or "billing" in p])

    def test_export_then_capture_with_one_more_turn_is_one_chat(self):
        self.put_export([chat()])
        self.run_import()
        turns = [msg("user", "first question", "r1"), msg("assistant", "first answer", "r2"),
                 msg("user", "second question", "r3"), msg("assistant", "a new reply", "r4")]
        self.put(capture("grok", "k1", turns), cid="k1", site="grok")
        rep = self.run_import()
        self.assertEqual((rep["written"], rep["conflict"], rep["error"]), (1, [], []))
        self.assertEqual(self.fake.sent()[1], ["a new reply"])
        self.assertEqual(len(self.fake.calls), 2)

    def test_reimport_unchanged_is_zero_calls(self):
        self.put_export([chat()])
        self.run_import()
        self.assertEqual(self.run_import()["nothing new"], 1)
        self.assertEqual(len(self.fake.calls), 1)


if __name__ == "__main__":
    unittest.main()
