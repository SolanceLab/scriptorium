# Scriptorium — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""The chat-notes command offers the same --api route as write_notes.py and says when it would use the login."""
import contextlib, io, json, os, sys, tempfile, unittest
from unittest import mock
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine"))
import import_captures as ic
import write_notes as wn


def _root():
    d = tempfile.mkdtemp()
    with open(os.path.join(d, "capture-claude-c1.json"), "w") as f:
        json.dump({"schema_version": 1, "site": "claude", "conversation_id": "c1", "title": "t", "exported_at": "2026-11-23T00:00:00Z",
                   "complete": True, "messages": [{"id": "m1", "role": "user", "text": "hello", "created_at": "2026-11-23T00:00:00Z"},
                                                  {"id": "m2", "role": "assistant", "text": "hi", "created_at": "2026-11-23T00:00:01Z"}]}, f)
    return d


class ApiRoute(unittest.TestCase):
    def test_api_without_key_is_refused(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}), self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            ic.main(["--root", _root(), "--api"])

    def test_api_route_calls_the_api_not_the_login(self):
        calls = []
        def fake_api(model, key, prompt, timeout):
            calls.append((model, key))
            return json.dumps({"memories": []}), {"total_cost_usd": 0}
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key-not-real"}), mock.patch.object(wn, "call_api", fake_api), \
                mock.patch.object(wn, "call_model", side_effect=AssertionError("login route used")), contextlib.redirect_stdout(io.StringIO()):
            try:
                ic.main(["--root", _root(), "--api"])
            except Exception:
                pass
        self.assertTrue(calls, "the API route was used")
        self.assertEqual(calls[0][1], "test-key-not-real")

    def test_login_route_says_so(self):
        err = io.StringIO()
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}), mock.patch.object(wn, "write_conversation", return_value={"memories": []}), \
                contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            ic.main(["--root", _root()])
        self.assertIn("Claude Code login", err.getvalue())
        self.assertIn("--api", err.getvalue())


if __name__ == "__main__":
    unittest.main()


class OneSetting(unittest.TestCase):
    def test_setting_alone_routes_both_commands_to_the_api(self):
        for main in (ic.main, wn.main):
            calls = []
            def fake_api(model, key, prompt, timeout, calls=calls):
                calls.append(key)
                return json.dumps({"memories": []}), {"total_cost_usd": 0}
            root = _root()
            args = ["--root", root] if main is ic.main else ["--export", os.path.join(root, "conversations.json"), "--out", os.path.join(root, "out")]
            if main is wn.main:
                with open(os.path.join(root, "conversations.json"), "w") as f:
                    json.dump([{"uuid": "c2", "name": "t", "created_at": "2026-11-23T00:00:00Z", "chat_messages": [
                        {"uuid": "m1", "sender": "human", "text": "hello", "created_at": "2026-11-23T00:00:00Z"}]}], f)
            with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key-not-real", "SCRIPTORIUM_NOTES_VIA": "api"}), \
                    mock.patch.object(wn, "call_api", fake_api), mock.patch.object(wn, "call_model", side_effect=AssertionError("login used")), \
                    contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                try:
                    main(args)
                except SystemExit:
                    pass
            self.assertTrue(calls, f"{main.__module__} used the API with the setting alone")

    def test_bad_setting_is_refused(self):
        with mock.patch.dict(os.environ, {"SCRIPTORIUM_NOTES_VIA": "apii"}), self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            ic.main(["--root", _root()])
