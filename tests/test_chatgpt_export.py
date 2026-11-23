# Scriptorium — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""ChatGPT's account export through the import path, with invented chats and a fake writer.
Run: python3 -m unittest test_chatgpt_export -v"""
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "engine"))
import chatgpt_export as gpt  # noqa: E402
from test_capture_import import Base, capture, msg  # noqa: E402


def node(nid, parent, role, parts, ctype="text", hidden=False, children=(), **content):
    m = {"id": nid, "author": {"role": role}, "create_time": 1700000000.0,
         "content": dict({"content_type": ctype, "parts": parts}, **content),
         "metadata": {"is_visually_hidden_from_conversation": True} if hidden else {}}
    return {"id": nid, "parent": parent, "children": list(children), "message": m}


def chat(cid="g1", current="n3", extra=None):
    mapping = {"root": {"id": "root", "parent": None, "children": ["n1"], "message": None},
               "n1": node("n1", "root", "user", ["first question"], children=["n2"]),
               "n2": node("n2", "n1", "assistant", ["first answer"], children=["n3"]),
               "n3": node("n3", "n2", "user", ["second question"])}
    mapping.update(extra or {})
    return {"id": cid, "title": "Invented", "create_time": 1700000000, "current_node": current, "mapping": mapping}


class Reading(Base):
    def put_export(self, convs, name="conversations.json"):
        path = os.path.join(self.root, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(convs, f)

    def texts(self, conv):
        return [(m["role"], m["text"]) for m in gpt.conversation_turns(conv)[2]]

    def test_linear_chat_in_order(self):
        self.assertEqual(self.texts(chat()), [("user", "first question"), ("assistant", "first answer"),
                                              ("user", "second question")])

    def test_branch_follows_current_node_only(self):
        extra = {"n2": node("n2", "n1", "assistant", ["first answer"], children=["n3", "n3b"]),
                 "n3b": node("n3b", "n2", "user", ["the unedited question"])}
        self.assertEqual(self.texts(chat(extra=extra))[-1], ("user", "second question"))
        self.assertEqual(self.texts(chat(current="n3b", extra=extra))[-1], ("user", "the unedited question"))
        self.assertNotIn("the unedited question", [t for _, t in self.texts(chat(extra=extra))])

    def test_hidden_system_tool_skipped_and_non_text_gets_a_placeholder(self):
        extra = {"n1": node("n1", "root", "system", ["be nice"], children=["h"]),
                 "h": node("h", "n1", "user", ["hidden"], hidden=True, children=["t"]),
                 "t": node("t", "h", "tool", ["tool output"], children=["i"]),
                 "i": node("i", "t", "user", [{"content_type": "image_asset_pointer"}, "look"], "multimodal_text",
                           children=["c"]),
                 "c": node("c", "i", "assistant", [], "code", text="print(1)", children=["n2"]),
                 "n2": node("n2", "c", "assistant", ["first answer"], children=["n3"])}
        got = self.texts(chat(extra=extra))
        self.assertEqual(got, [("user", "[image]\nlook"), ("assistant", "print(1)"),
                               ("assistant", "first answer"), ("user", "second question")])

    def test_assistant_tool_call_messages_are_skipped(self):
        call = node("c", "n2", "assistant", ['{"q": "raw payload"}'], children=["n3"])
        call["message"]["recipient"] = "web"
        extra = {"n2": node("n2", "n1", "assistant", ["first answer"], children=["c"]), "c": call,
                 "n3": node("n3", "c", "user", ["second question"])}
        self.assertEqual(self.texts(chat(extra=extra)), [("user", "first question"), ("assistant", "first answer"),
                                                         ("user", "second question")])

    def test_broken_trees_do_not_crash(self):
        self.assertEqual(gpt.conversation_turns({"id": "x", "mapping": {}, "current_node": "nope"})[2], [])
        loop = {"a": {"id": "a", "parent": "b", "message": None}, "b": {"id": "b", "parent": "a", "message": None}}
        self.assertEqual(gpt.conversation_turns({"id": "x", "mapping": loop, "current_node": "a"})[2], [])

    def test_claude_and_chatgpt_exports_in_one_root_do_not_mix(self):
        claude = {"uuid": "c1", "name": "Claude chat", "chat_messages": [
            {"uuid": "u1", "sender": "human", "text": "claude question"},
            {"uuid": "u2", "sender": "assistant", "text": "claude answer"}]}
        self.put_export([claude, chat("c1")])          # the same id in both worlds stays two chats
        self.assertEqual(self.run_import()["written"], 5)
        self.assertIn(["claude question", "claude answer"], self.fake.sent())
        self.assertIn(["first question", "first answer", "second question"], self.fake.sent())
        self.assertEqual(len(self.fake.calls), 2)
        self.assertEqual(self.notes("claude", "c1")["title"], "Claude chat")
        self.assertEqual(self.notes("chatgpt", "c1")["title"], "Invented")

    def test_renamed_and_subfolder_exports_are_read(self):
        self.put_export([chat("r1")], "chatgpt-conversations.json")
        self.put_export([chat("s1")], os.path.join("unzipped", "conversations.json"))
        self.assertEqual(self.run_import()["written"], 6)

    def test_export_then_capture_with_one_more_turn_is_one_chat(self):
        self.put_export([chat()])
        self.run_import()
        self.assertEqual(len(self.fake.calls), 1)
        turns = [msg("user", "first question", "n1"), msg("assistant", "first answer", "n2"),
                 msg("user", "second question", "n3"), msg("assistant", "a new reply", "n4")]
        self.put(capture("chatgpt", "g1", turns), cid="g1")
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
