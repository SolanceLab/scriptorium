# Scriptorium — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""Saved chats: the capture file and `import`, with a fake writer and invented chats.
Run: python3 -m unittest test_capture_import -v"""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "engine"))
import capture_file as cf  # noqa: E402
import import_captures as ic  # noqa: E402
import write_notes as wn  # noqa: E402


def msg(role, text, mid=None, at=None):
    return {k: v for k, v in {"id": mid, "role": role, "text": text, "created_at": at}.items() if v}


def capture(site="chatgpt", cid="abc", messages=None, **kw):
    d = {"schema_version": 1, "site": site, "conversation_id": cid, "title": "A chat",
         "exported_at": "2026-11-23T10:00:00Z", "complete": True,
         "messages": messages if messages is not None else [msg("user", "hello there friend", "m1"),
                                                            msg("assistant", "hi, how are you", "m2")]}
    d.update(kw)
    return d


class Fake:
    """A fake notes writer: one note per message, citing its id; records every call."""
    def __init__(self):
        self.calls = []

    def __call__(self, conv):
        self.calls.append(conv)
        lines = [(m["uuid"], m["text"]) for m in conv["chat_messages"]]
        return {"conversation_id": conv["uuid"], "title": conv["name"], "session_date": "2026-11-23",
                "memories": [{"source_line_ids": [lid], "excerpt": t} for lid, t in lines],
                "rejected": [], "calls": [{}]}

    def sent(self):
        return [[m["text"] for m in c["chat_messages"]] for c in self.calls]


class Base(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.root = self._t.name
        self.out = os.path.join(self.root, "notes")
        self.fake = Fake()

    def tearDown(self):
        self._t.cleanup()

    def put(self, data, name=None, site="chatgpt", cid="abc"):
        path = os.path.join(self.root, name or cf.capture_name(site, cid))
        with open(path, "w") as f:
            json.dump(data, f)
        return path

    def run_import(self):
        return ic.import_root(self.root, self.out, self.fake)

    def notes(self, site="chatgpt", cid="abc"):
        with open(os.path.join(self.out, ic.notes_name(site, cid))) as f:
            return json.load(f)


class CaptureFile(Base):
    def test_valid_file_reads(self):
        got = cf.read_capture(self.put(capture(raw={"x": 1})))
        self.assertEqual((got["site"], got["conversation_id"], len(got["messages"])), ("chatgpt", "abc", 2))

    def test_bad_files_give_a_clear_error_naming_the_file(self):
        cases = {"schema": capture(schema_version=2), "site": capture(site="gemini"),
                 "id": capture(conversation_id=""), "incomplete": capture(complete=False),
                 "role": capture(messages=[{"role": "system", "text": "x"}])}
        for label, data in cases.items():
            p = self.put(data, name=cf.capture_name("chatgpt", "abc"))
            with self.assertRaises(cf.CaptureError, msg=label) as cm:
                cf.read_capture(p)
            self.assertIn("capture-chatgpt-abc.json", str(cm.exception))

    def test_wrong_site_for_file_name_and_garbage(self):
        p = self.put(capture(site="grok"), name=cf.capture_name("chatgpt", "abc"))
        with self.assertRaises(cf.CaptureError):
            cf.read_capture(p)
        p = os.path.join(self.root, cf.capture_name("claude", "z"))
        open(p, "w").write("{not json")
        with self.assertRaises(cf.CaptureError):
            cf.read_capture(p)

    def test_filename_encoding_round_trips(self):
        for cid in ("abc", "a/b c", "..", "x:y?z%41", "ünï/cødé", "-lead-dash"):
            for site in cf.SITES:
                name = cf.capture_name(site, cid)
                self.assertNotIn(os.sep, name)
                self.assertEqual(cf.parse_name(name), (site, cid))

    def test_bad_file_does_not_stop_the_others(self):
        self.put(capture(complete=False), cid="bad", name=cf.capture_name("chatgpt", "bad"))
        self.put(capture())
        rep = self.run_import()
        self.assertEqual(len(rep["error"]), 1)
        self.assertIn("capture-chatgpt-bad.json", rep["error"][0])
        self.assertEqual(rep["written"], 2)


class Importing(Base):
    def test_first_import_one_call_with_all_messages_then_zero(self):
        self.put(capture())
        self.run_import()
        self.assertEqual(self.fake.sent(), [["hello there friend", "hi, how are you"]])
        self.assertEqual(self.run_import()["nothing new"], 1)
        self.assertEqual(len(self.fake.calls), 1)

    def test_one_added_message_sends_only_that_message(self):
        self.put(capture())
        self.run_import()
        self.put(capture(messages=capture()["messages"] + [msg("user", "one more thing", "m3")]))
        self.run_import()
        self.assertEqual(self.fake.sent()[1], ["one more thing"])

    def test_messages_without_ids_and_identical_turns_both_survive(self):
        turns = [msg("user", "ok"), msg("assistant", "right"), msg("user", "ok"), msg("assistant", "right")]
        self.put(capture("grok", "g1", turns), cid="g1", site="grok")
        self.run_import()
        self.assertEqual(self.fake.sent(), [["ok", "right", "ok", "right"]])
        self.assertEqual(self.run_import()["nothing new"], 1)
        self.put(capture("grok", "g1", turns + [msg("user", "ok")]), cid="g1", site="grok")
        self.run_import()
        self.assertEqual(self.fake.sent()[1], ["ok"])
        ids = [m["uuid"] for c in self.fake.calls for m in c["chat_messages"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_changed_history_is_a_conflict_and_changes_nothing(self):
        self.put(capture())
        self.run_import()
        before = self.notes()
        self.put(capture(messages=[msg("user", "something else entirely", "z9")]))
        rep = self.run_import()
        self.assertEqual(len(rep["conflict"]), 1)
        self.assertEqual(len(self.fake.calls), 1)
        self.assertEqual(self.notes(), before)

    def _claude_export(self, extra=()):
        chat = [{"uuid": "m1", "sender": "human", "text": "hello there friend", "created_at": "2026-11-20T10:00:00Z"},
                {"uuid": "m2", "sender": "assistant", "text": "hi, how are you", "created_at": "2026-11-20T10:01:00Z"},
                *extra]
        with open(os.path.join(self.root, "conversations.json"), "w") as f:
            json.dump([{"uuid": "abc", "name": "A chat", "created_at": "2026-11-20T10:00:00Z",
                        "chat_messages": chat}], f)

    def test_claude_capture_and_export_are_one_chat_either_order(self):
        extra = [{"uuid": "m3", "sender": "human", "text": "a third message", "created_at": "2026-11-20T10:02:00Z"}]
        # export first, capture later
        self._claude_export()
        self.run_import()
        self.put(capture("claude", "abc", capture()["messages"] + [msg("user", "a third message", "m3")]),
                 site="claude")
        self.run_import()
        self.assertEqual(self.fake.sent(), [["hello there friend", "hi, how are you"], ["a third message"]])
        self.assertEqual(len(os.listdir(self.out)), 1)
        # capture first, export later (fresh root)
        self.setUp()
        self.put(capture("claude", "abc"), site="claude")
        self.run_import()
        self._claude_export(extra)
        self.run_import()
        self.assertEqual(self.fake.sent(), [["hello there friend", "hi, how are you"], ["a third message"]])
        self.assertEqual(len(os.listdir(self.out)), 1)

    def test_same_raw_id_on_two_sites_is_two_chats(self):
        self.put(capture("chatgpt", "abc"), site="chatgpt")
        self.put(capture("grok", "abc"), site="grok")
        self._claude_export()
        self.run_import()
        self.assertEqual(len(self.fake.calls), 3)
        self.assertEqual(len(os.listdir(self.out)), 3)

    def test_crash_before_notes_reruns_the_batch_once(self):
        self.put(capture())

        def boom(conv):
            raise RuntimeError("writer died")
        with self.assertRaises(RuntimeError):
            ic.import_root(self.root, self.out, boom)
        self.run_import()
        self.run_import()
        self.assertEqual(self.fake.sent(), [["hello there friend", "hi, how are you"]])
        self.assertEqual(len(self.notes()["memories"]), 2)

    def test_crash_after_notes_neither_skips_nor_doubles(self):
        self.put(capture())
        real = wn.write_atomic
        state_writes = []

        def flaky(path, obj):
            if ".state.json" in path:
                state_writes.append(1)
                if len(state_writes) == 2:        # the commit that follows the notes
                    raise OSError("died before commit")
            real(path, obj)
        with patch.object(wn, "write_atomic", flaky):
            with self.assertRaises(OSError):
                self.run_import()
        self.assertEqual(len(self.notes()["memories"]), 2)      # notes were written
        self.run_import()                                      # recovery: commit only
        self.assertEqual(len(self.fake.calls), 1)              # the writer is not called a second time
        self.assertEqual(len(self.notes()["memories"]), 2)
        self.assertEqual(self.run_import()["nothing new"], 1)

    def test_earlier_notes_survive_the_second_batch(self):
        self.put(capture())
        self.run_import()
        self.put(capture(messages=capture()["messages"] + [msg("user", "one more thing", "m3")]))
        self.run_import()
        mem = self.notes()["memories"]
        self.assertEqual([m["source_line_ids"] for m in mem], [["m1"], ["m2"], ["m3"]])
        self.assertEqual(len(self.notes()["batches"]), 2)

    def test_writer_error_commits_nothing_and_retries(self):
        self.put(capture())
        bad = lambda conv: {"error": "model timed out", "memories": []}  # noqa: E731
        rep = ic.import_root(self.root, self.out, bad)
        self.assertEqual(len(rep["error"]), 1)
        self.assertFalse(os.path.exists(os.path.join(self.out, ic.notes_name("chatgpt", "abc"))))
        self.run_import()
        self.assertEqual(len(self.notes()["memories"]), 2)

    def test_unrecorded_existing_notes_are_a_conflict(self):
        self.put(capture())
        os.makedirs(self.out)
        with open(os.path.join(self.out, ic.notes_name("chatgpt", "abc")), "w") as f:
            f.write("{}")
        self.assertEqual(len(self.run_import()["conflict"]), 1)
        self.assertEqual(self.fake.calls, [])


class PlainBehaviour(Base):
    def test_no_capture_files_leaves_write_notes_as_before(self):
        conv = {"uuid": "c1", "name": "Plain", "created_at": "2026-11-20T10:00:00Z",
                "chat_messages": [{"uuid": "u1", "sender": "human", "text": "hello there friend"}]}
        export = os.path.join(self.root, "conversations.json")
        json.dump([conv], open(export, "w"))
        cmd = "python3 -c \"import sys; sys.stdin.read(); print('[]')\""
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            wn.main(["--export", export, "--out", self.out, "--model-cmd", cmd, "--prompt", "public"])
        with open(os.path.join(self.out, "c1.json")) as f:
            res = json.load(f)
        self.assertEqual((res["conversation_id"], res["memories"]), ("c1", []))
        self.assertNotIn("batches", res)
        self.assertFalse(os.path.exists(os.path.join(self.root, ic.STATE_DIR)))


class ReviewFixes(Base):
    def _fail_once_state(self):
        bad = lambda conv: {"error": "timed out", "memories": []}  # noqa: E731
        ic.import_root(self.root, self.out, bad)

    def test_unreadable_or_non_dict_notes_are_left_untouched(self):
        self.put(capture())
        os.makedirs(self.out)
        path = os.path.join(self.out, ic.notes_name("chatgpt", "abc"))
        state = ic._state_path(self.root, "chatgpt", "abc")
        os.makedirs(os.path.dirname(state))
        with open(state, "w") as f:
            json.dump({"processed": [{"id": "m1", "ck": "x"}], "pending": None}, f)
        for body in ("{not json", "[1, 2]"):
            with open(path, "w") as f:
                f.write(body)
            rep = self.run_import()
            self.assertEqual(len(rep["error"]), 1)
            with open(path) as f:
                self.assertEqual(f.read(), body)
        self.assertEqual(self.fake.calls, [])

    def test_stale_pending_batch_is_recomputed_from_the_source(self):
        self.put(capture())
        self._fail_once_state()
        self.put(capture(messages=[msg("user", "hello there friend", "m1"), msg("assistant", "edited reply", "m2")]))
        self.run_import()
        self.assertEqual(self.fake.sent(), [["hello there friend", "edited reply"]])
        self.assertEqual(self.run_import()["nothing new"], 1)

    def test_bad_timezone_is_refused_before_any_state_is_written(self):
        self.put(capture())
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            ic.main(["--root", self.root, "--timezone", "Not/AZone", "--model-cmd", "true"])
        self.assertFalse(os.path.exists(os.path.join(self.root, ic.STATE_DIR)))

    def test_claude_and_other_site_ids_never_share_a_notes_file(self):
        names = {ic.notes_name("claude", "chatgpt-abc"), ic.notes_name("chatgpt", "abc"),
                 ic.notes_name("claude", "a/b"), ic.notes_name("claude", "a b"), ic.notes_name("claude", "a_b")}
        self.assertEqual(len(names), 5)


class ReviewFixes2(Base):
    def test_duplicate_message_ids_finish_and_stay_given_notes_once(self):
        self.put(capture(messages=[msg("user", "first", "d"), msg("assistant", "second", "d")]))
        ic_report = self.run_import()
        self.assertEqual(ic_report["written"], 2)
        self.assertEqual(self.run_import()["nothing new"], 1)

    def test_saved_batch_with_history_gone_is_a_conflict_before_any_write(self):
        self.put(capture())
        self.run_import()
        self.put(capture(messages=[msg("user", "hello there friend", "m1"), msg("assistant", "hi, how are you", "m2"),
                                   msg("user", "more", "m3")]))
        state = ic._state_path(self.root, "chatgpt", "abc")
        with open(state) as f:
            st = json.load(f)
        st["pending"] = {"id": "zz", "messages": [{"id": "m3", "ck": ic._prepare([msg("user", "more", "m3")])[0]["ck"],
                                                    "role": "user", "text": "more", "created_at": None}]}
        st["processed"].append({"id": "gone", "ck": "gone"})
        with open(state, "w") as f:
            json.dump(st, f)
        before = open(os.path.join(self.out, ic.notes_name("chatgpt", "abc"))).read()
        rep = self.run_import()
        self.assertEqual(len(rep["conflict"]), 1)
        self.assertEqual(open(os.path.join(self.out, ic.notes_name("chatgpt", "abc"))).read(), before)

    def test_malformed_state_and_null_batches_are_reported_not_crashes(self):
        self.put(capture())
        state = ic._state_path(self.root, "chatgpt", "abc")
        os.makedirs(os.path.dirname(state))
        for body in ("[1]", "{not json", '{"processed": null}'):
            with open(state, "w") as f:
                f.write(body)
            self.assertEqual(len(self.run_import()["error"]), 1)
            with open(state) as f:
                self.assertEqual(f.read(), body)
        self.assertEqual(ic.merge_notes({"memories": [], "batches": None}, {"memories": []}, "b")["batches"], ["b"])

    def test_schema_version_true_is_refused(self):
        path = self.put(capture(schema_version=True))
        with self.assertRaises(cf.CaptureError):
            cf.read_capture(path)


if __name__ == "__main__":
    unittest.main()
