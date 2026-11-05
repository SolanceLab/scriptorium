# Scriptorium — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""The MCP server: keyed, local only, paths inside one folder, a model name but never a command.
Run: python3 -m unittest discover -s engine/tests"""
import json
import os
import socket
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
for _d in (os.path.join(HERE, "..", "..", "mcp"), os.path.join(HERE, "..", "mcp")):
    if os.path.isfile(os.path.join(_d, "server.py")):
        sys.path.insert(0, _d)
import server  # noqa: E402

ANSWER = [{"topic": "Creative", "source_line_ids": ["D1:1"], "source_session_date": "14 March, 2022",
           "excerpt": "I finally signed up for the pottery class.", "summary": "Priya signed up for a pottery class."}]
KEY = "k" * 32
CONV = [{"uuid": "c1", "name": "3:20 pm on 14 March, 2022", "created_at": "2022-03-14T15:20:00Z", "chat_messages": [
    {"uuid": "D1:1", "sender": "human", "text": "Priya: I finally signed up for the pottery class."}]}]


class Tools(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.realpath(self.tmp.name)
        server.configure(self.root, KEY)
        with open(os.path.join(self.root, "conversations.json"), "w") as f:
            json.dump(CONV, f)
        fake = os.path.join(self.root, "fake_model.py")
        with open(fake, "w") as f:
            f.write("import sys\nsys.stdin.read()\nprint(%r)\n" % json.dumps(ANSWER))
        os.environ["SCRIPTORIUM_MODEL_CMD"] = f"{sys.executable} {fake}"

    def tearDown(self):
        os.environ.pop("SCRIPTORIUM_MODEL_CMD", None)
        self.tmp.cleanup()

    def test_old_env_names_are_still_read(self):
        for k in ("SCRIPTORIUM_MODEL_CMD", "SCRIPTORIUM_TZ", "MEMORY_ENTRY_MODEL_CMD", "MEMORY_ENTRY_TZ"):
            os.environ.pop(k, None)
        try:
            os.environ["MEMORY_ENTRY_MODEL_CMD"] = "oldcmd"  # the old name
            os.environ["MEMORY_ENTRY_TZ"] = "UTC"
            self.assertEqual(server.model_args({}), ["--model-cmd", "oldcmd"])
            self.assertTrue(server.tz_args({}))
            os.environ["SCRIPTORIUM_MODEL_CMD"] = "newcmd"
            self.assertEqual(server.model_args({}), ["--model-cmd", "newcmd"])
        finally:
            for k in ("MEMORY_ENTRY_MODEL_CMD", "MEMORY_ENTRY_TZ", "SCRIPTORIUM_TZ"):
                os.environ.pop(k, None)

    def call(self, name, args):
        return server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                              "params": {"name": name, "arguments": args}})["result"]

    def test_lists_two_tools(self):
        out = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        self.assertEqual([t["name"] for t in out["result"]["tools"]], ["write_memories", "write_people"])

    def test_writes_memories_inside_the_folder(self):
        res = self.call("write_memories", {"export": "conversations.json", "out": "mem"})
        self.assertFalse(res["isError"], res)
        self.assertTrue(os.path.isfile(os.path.join(self.root, "mem", "c1.json")))

    def test_write_people_runs_through_the_server(self):
        with open(os.path.join(self.root, "fake_people.py"), "w") as f:
            f.write("import sys\nsys.stdin.read()\nprint('[]')\n")
        os.environ["SCRIPTORIUM_MODEL_CMD"] = f"{sys.executable} {os.path.join(self.root, 'fake_people.py')}"
        os.makedirs(os.path.join(self.root, "mem"))
        with open(os.path.join(self.root, "mem", "c1.json"), "w") as f:
            json.dump({"conversation_id": "c1", "session_date": "2022-03-14", "memories": [
                {"topic": "Creative", "source_line_ids": ["D1:1"], "excerpt": "I finally signed up for the pottery class.",
                 "summary": "Priya signed up for a pottery class."}]}, f)
        res = self.call("write_people", {"export": "conversations.json", "memories": "mem", "out": "people"})
        self.assertFalse(res["isError"], res)

    def test_a_path_outside_the_folder_is_refused(self):
        for bad in ({"export": "/etc/passwd", "out": "mem"}, {"export": "conversations.json", "out": "../escape"}):
            res = self.call("write_memories", bad)
            self.assertTrue(res["isError"])
            self.assertIn("outside the server's folder", res["content"][0]["text"])

    def test_a_link_out_of_the_folder_is_refused(self):
        os.symlink("/etc", os.path.join(self.root, "sneaky"))
        res = self.call("write_memories", {"export": "conversations.json", "out": "sneaky/mem"})
        self.assertTrue(res["isError"])

    def test_a_model_is_a_name_never_a_command(self):
        os.environ.pop("SCRIPTORIUM_MODEL_CMD")
        res = self.call("write_memories", {"export": "conversations.json", "out": "mem", "model": "x; rm -rf ~"})
        self.assertTrue(res["isError"])
        self.assertIn("model must be a model id", res["content"][0]["text"])

    def test_a_time_zone_is_a_real_zone_never_an_option(self):
        for bad in ("Mars/Olympus", "--model-cmd=x"):
            res = self.call("write_memories", {"export": "conversations.json", "out": "mem", "timezone": bad})
            self.assertTrue(res["isError"])
            self.assertIn("timezone must be", res["content"][0]["text"])
        res = self.call("write_memories", {"export": "conversations.json", "out": "mem", "timezone": "Asia/Jakarta"})
        self.assertFalse(res["isError"], res)

    def test_limit_is_bounded(self):
        res = self.call("write_memories", {"export": "conversations.json", "out": "mem", "limit": 10_000})
        self.assertTrue(res["isError"])

    def test_unknown_tool_is_an_error(self):
        out = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "rm"}})
        self.assertEqual(out["error"]["code"], -32602)

    def test_refuses_to_start_without_a_key_or_with_home_as_the_folder(self):
        with self.assertRaises(SystemExit):
            server.configure(self.root, "")
        with self.assertRaises(SystemExit):
            server.configure(os.path.expanduser("~"), KEY)


class Http(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        server.configure(cls.tmp.name, KEY)
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        cls.port = s.getsockname()[1]
        s.close()
        cls.httpd = server.ThreadingHTTPServer(("127.0.0.1", cls.port), server.Handler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.tmp.cleanup()

    def post(self, headers, content_type="application/json"):
        base = {"Content-Type": content_type} if content_type else {}
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/mcp", method="POST",
                                     data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}).encode(),
                                     headers=dict(base, **headers))
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status
        except urllib.error.HTTPError as e:
            e.close()
            return e.code

    def test_the_right_key_is_answered(self):
        self.assertEqual(self.post({"Authorization": f"Bearer {KEY}"}), 200)

    def test_a_wrong_or_missing_key_is_refused(self):
        self.assertEqual(self.post({"Authorization": "Bearer nope"}), 401)
        self.assertEqual(self.post({}), 401)

    def test_a_foreign_host_or_origin_is_refused(self):
        self.assertEqual(self.post({"Authorization": f"Bearer {KEY}", "Host": "evil.example"}), 403)
        self.assertEqual(self.post({"Authorization": f"Bearer {KEY}", "Origin": "https://evil.example"}), 403)
        self.assertEqual(self.post({"Authorization": f"Bearer {KEY}", "Origin": "null"}), 403)

    def test_a_body_that_is_not_json_is_refused(self):
        self.assertEqual(self.post({"Authorization": f"Bearer {KEY}"}, content_type="text/plain"), 415)

    def test_a_missing_host_is_refused(self):
        self.assertFalse(server.local_request({}))
        self.assertTrue(server.local_request({"Host": "127.0.0.1:8795"}))


class Malformed(unittest.TestCase):
    def test_params_that_are_not_an_object_answer_an_error_not_a_crash(self):
        for msg in ({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": [1]},
                    {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": ["x"]}},
                    {"jsonrpc": "2.0", "id": 1, "method": "ping", "params": None},
                    {"jsonrpc": "2.0", "id": 1, "method": "ping", "params": []},
                    {"jsonrpc": "2.0", "id": 1, "method": "ping", "params": 0},
                    {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                     "params": {"name": "write_memories", "arguments": []}}):
            self.assertEqual(server.handle(msg)["error"]["code"], -32602, msg)


class Links(unittest.TestCase):
    def test_a_linked_memory_file_is_never_read(self):
        sys.path.insert(0, server.ENGINE)
        import write_people as wp
        with tempfile.TemporaryDirectory() as tmp:
            outside = os.path.join(tmp, "outside.json")
            with open(outside, "w") as f:
                json.dump({"memories": [{"summary": "secret from outside", "source_line_ids": ["X"]}]}, f)
            mem = os.path.join(tmp, "mem")
            os.makedirs(mem)
            os.symlink(outside, os.path.join(mem, "link.json"))
            self.assertEqual(wp.load_memories(mem), [])


if __name__ == "__main__":
    unittest.main()
