# Scriptorium — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""The chat reader's output is called notes: the new tool and file names, the old names that still work, the
old output that still reads, and the words the docs use.
Run: python3 -m unittest discover -s tests"""
import ast
import contextlib
import http.client
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.realpath(os.path.join(HERE, ".."))
for _d in (os.path.join(ROOT, "mcp"), os.path.join(ROOT, "engine")):
    sys.path.insert(0, _d)
import server  # noqa: E402
import write_notes  # noqa: E402
import write_memories  # noqa: E402
import nightly  # noqa: E402
import write_people  # noqa: E402
import found_in_chats  # noqa: E402
import roster  # noqa: E402
import review  # noqa: E402
import suggest_fields  # noqa: E402

ANSWER = [{"topic": "Creative", "source_line_ids": ["D1:1"], "source_session_date": "14 March, 2022",
           "excerpt": "I finally signed up for the pottery class.", "summary": "Priya signed up for a pottery class."}]
KEY = "k" * 32
CONV = [{"uuid": "c1", "name": "3:20 pm on 14 March, 2022", "created_at": "2022-03-14T15:20:00Z", "chat_messages": [
    {"uuid": "D1:1", "sender": "human", "text": "Priya: I finally signed up for the pottery class."}]}]


def summary(text):
    """The run summary the writer prints as JSON: the last line that starts with a brace."""
    lines = [ln for ln in text.splitlines() if ln.startswith("{")]
    return json.loads(lines[-1])


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f)


def fake_model(path, answer):
    with open(path, "w", encoding="utf-8") as f:
        f.write("import sys\nsys.stdin.read()\nprint(%r)\n" % json.dumps(answer))
    return "%s %s" % (sys.executable, path)


class NotesOverHttp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.realpath(self.tmp.name)
        server.configure(self.root, KEY)
        write_json(os.path.join(self.root, "conversations.json"), CONV)
        os.environ["SCRIPTORIUM_MODEL_CMD"] = fake_model(os.path.join(self.root, "fake_model.py"), ANSWER)
        self.http_server = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.thread = threading.Thread(target=self.http_server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.thread.join)
        self.addCleanup(self.http_server.server_close)
        self.addCleanup(self.http_server.shutdown)

    def tearDown(self):
        os.environ.pop("SCRIPTORIUM_MODEL_CMD", None)
        self.tmp.cleanup()

    def post(self, method, params=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.http_server.server_address[1], timeout=60)
        try:
            body = {"jsonrpc": "2.0", "id": 1, "method": method}
            if params is not None:
                body["params"] = params
            conn.request("POST", "/mcp", json.dumps(body),
                         {"Authorization": "Bearer " + KEY, "Content-Type": "application/json"})
            return json.loads(conn.getresponse().read())["result"]
        finally:
            conn.close()

    def call(self, name, out):
        return self.post("tools/call", {"name": name, "arguments": {"export": "conversations.json", "out": out}})

    def test_speaker_tags_come_from_server_environment_only(self):
        prompt_path = os.path.join(self.root, "captured.txt")
        model_path = os.path.join(self.root, "fake_model.py")
        with open(model_path, "w", encoding="utf-8") as f:
            f.write(f"import sys\nwith open({prompt_path!r}, 'w', encoding='utf-8') as out:\n"
                    f"    out.write(sys.stdin.read())\nprint({json.dumps(ANSWER)!r})\n")
        with patch.dict(os.environ, {"SCRIPTORIUM_USER_NAME": "Morgan", "SCRIPTORIUM_AGENT_NAME": "Casey",
                                     "SCRIPTORIUM_PLATFORM": "Discord"}):
            for name, out in (("write_notes", "new"), ("write_memories", "legacy")):
                with self.subTest(tool=name):
                    res = self.post("tools/call", {"name": name, "arguments": {
                        "export": "conversations.json", "out": out,
                        "user_name": "RequestUser", "agent_name": "RequestAgent", "platform": "RequestPlatform",
                    }})
                    self.assertFalse(res["isError"], res)
                    with open(prompt_path, encoding="utf-8") as f:
                        prompt = f.read()
                    self.assertIn('Tag user as "Morgan" and agent as "Casey · Discord".', " ".join(prompt.split()))
                    self.assertNotIn("RequestPlatform", prompt)
                    self.assertNotIn("RequestUser", prompt)
                    self.assertNotIn("RequestAgent", prompt)

    def test_tools_list_shows_both_names(self):
        tools = {t["name"]: t for t in self.post("tools/list")["tools"]}
        names = [t["name"] for t in self.post("tools/list")["tools"]]
        self.assertIn("write_notes", tools)
        self.assertIn("write_memories", tools)
        self.assertLess(names.index("write_notes"), names.index("write_memories"))
        self.assertEqual(tools["write_notes"]["inputSchema"], tools["write_memories"]["inputSchema"])
        self.assertIn("write_notes", tools["write_memories"]["description"])

    def test_write_notes_writes_a_notes_file(self):
        res = self.call("write_notes", "notes_a")
        self.assertIs(res["isError"], False)
        self.assertEqual(summary(res["content"][0]["text"])["written"], 1)
        doc = read_json(os.path.join(self.root, "notes_a", "c1.json"))
        self.assertEqual(len(doc["memories"]), 1)
        self.assertEqual(doc["memories"][0]["source_line_ids"], ["D1:1"])

    def test_write_memories_alias_writes_the_same(self):
        new = self.call("write_notes", "notes_b")
        old = self.call("write_memories", "mem_b")
        self.assertEqual(summary(new["content"][0]["text"])["written"], 1)
        self.assertEqual(summary(old["content"][0]["text"])["written"], 1)
        a = read_json(os.path.join(self.root, "notes_b", "c1.json"))["memories"]
        b = read_json(os.path.join(self.root, "mem_b", "c1.json"))["memories"]
        self.assertEqual(a, b)

    def test_names_continue_each_other(self):
        self.call("write_notes", "run")
        res = self.call("write_memories", "run")
        s = summary(res["content"][0]["text"])
        self.assertEqual(s["skipped_finished"], 1)
        self.assertEqual(s["written"], 0)


class ModuleAlias(unittest.TestCase):
    def test_old_import_is_the_new_module(self):
        self.assertIs(write_memories, write_notes)
        for mod in (nightly, roster, review, found_in_chats, write_people, suggest_fields):
            with self.subTest(module=mod.__name__):
                self.assertIs(mod.wm, write_notes)

    def test_old_script_path_still_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            export, out = os.path.join(tmp, "conversations.json"), os.path.join(tmp, "out")
            write_json(export, CONV)
            cmd = fake_model(os.path.join(tmp, "fake_model.py"), ANSWER)
            argv = [sys.executable, os.path.join(ROOT, "engine", "write_memories.py"), "--export", export,
                    "--out", out, "--model-cmd", cmd, "--prompt", "public"]
            proc = subprocess.run(argv, capture_output=True, text=True, cwd=tmp)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertEqual(summary(proc.stdout)["written"], 1)
            self.assertEqual(len(read_json(os.path.join(out, "c1.json"))["memories"]), 1)

    def test_notes_writer_does_not_import_pen(self):
        with open(os.path.join(ROOT, "engine", "write_notes.py"), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        imports = [n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
        imports += [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
        self.assertFalse(any("pen" in x.split(".") for x in imports))


# Two sessions of Mira, three lines. The fake writer model turns every "Mira: ..." line into one note.
MIRA_EXPORT = [
    {"uuid": "s1", "name": "s1", "created_at": "2023-05-08T10:00:00Z", "chat_messages": [
        {"uuid": "D1:1", "sender": "human", "text": "Mira: I moved to Lisbon."},
        {"uuid": "D1:2", "sender": "human", "text": "Mira: I started a pottery class."}]},
    {"uuid": "s2", "name": "s2", "created_at": "2023-06-12T10:00:00Z", "chat_messages": [
        {"uuid": "D2:1", "sender": "human", "text": "Mira: I baked a dairy-free chocolate cake for my sister."}]},
]
MIRA_CARD = [{"person": "Mira", "aspect": "life", "memories": ["M1", "M2", "M3"],
              "summary": "Mira moved to Lisbon and started a pottery class (8 May 2023), then baked a cake for her "
                         "sister (12 June 2023)."}]
FAKE_FACTS = '''import json, re, sys
prompt = sys.stdin.read()
ids = set(re.findall(r"^\\[([^\\]]+)\\] ", prompt, re.M))
canned = json.load(open(%r))
print(json.dumps({"facts": [c for c in canned if c["message_id"] in ids]}))
'''
LINE_WRITER = '''import json, re, sys
prompt = sys.stdin.read()
out = []
for lid, text in re.findall(r"^\\[([^\\]]+)\\] user: Mira: (.*)$", prompt, re.M):
    out.append({"topic": "Personal", "source_line_ids": [lid], "excerpt": text, "summary": "Mira said: " + text,
                "source_session_date": "x"})
print(json.dumps(out))
'''


# Output of the real v1.4.0 writers (git show v1.4.0:engine/write_memories.py and write_people.py), run once on
# MIRA_EXPORT with LINE_WRITER and MIRA_CARD, stored here so the test needs neither the tag nor the shim.
V140_OUTPUT = json.loads(r'''
{
 "notes": {
  "s1.json": {
   "conversation_id": "s1",
   "title": "s1",
   "session_date": "2023-05-08",
   "memories": [
    {
     "topic": "Personal",
     "source_line_ids": [
      "D1:1"
     ],
     "excerpt": "I moved to Lisbon.",
     "summary": "Mira said: I moved to Lisbon.",
     "source_session_date": "2023-05-08",
     "salience": 0.5,
     "valence": 0.0,
     "emotions": [],
     "context_tags": []
    },
    {
     "topic": "Personal",
     "source_line_ids": [
      "D1:2"
     ],
     "excerpt": "I started a pottery class.",
     "summary": "Mira said: I started a pottery class.",
     "source_session_date": "2023-05-08",
     "salience": 0.5,
     "valence": 0.0,
     "emotions": [],
     "context_tags": []
    }
   ],
   "rejected": [],
   "calls": [
    {}
   ],
   "writer": {
    "model_cmd": "python3 fake_model.py",
    "claude_version": null,
    "writer_commit": null,
    "writer_dirty": false,
    "prompt_file": "prompt-public.md",
    "prompt_sha256": "6d520899d7d3ff77f4bce8e6314626952185886ec168cb81481948b603feeb84",
    "env_passed": [
     "HOME",
     "LANG",
     "LOGNAME",
     "PATH",
     "TERM",
     "TMPDIR",
     "USER"
    ],
    "timezone": "as exported",
    "seconds": 0.0
   }
  },
  "s2.json": {
   "conversation_id": "s2",
   "title": "s2",
   "session_date": "2023-06-12",
   "memories": [
    {
     "topic": "Personal",
     "source_line_ids": [
      "D2:1"
     ],
     "excerpt": "I baked a dairy-free chocolate cake for my sister.",
     "summary": "Mira said: I baked a dairy-free chocolate cake for my sister.",
     "source_session_date": "2023-06-12",
     "salience": 0.5,
     "valence": 0.0,
     "emotions": [],
     "context_tags": []
    }
   ],
   "rejected": [],
   "calls": [
    {}
   ],
   "writer": {
    "model_cmd": "python3 fake_model.py",
    "claude_version": null,
    "writer_commit": null,
    "writer_dirty": false,
    "prompt_file": "prompt-public.md",
    "prompt_sha256": "6d520899d7d3ff77f4bce8e6314626952185886ec168cb81481948b603feeb84",
    "env_passed": [
     "HOME",
     "LANG",
     "LOGNAME",
     "PATH",
     "TERM",
     "TMPDIR",
     "USER"
    ],
    "timezone": "as exported",
    "seconds": 0.0
   }
  }
 },
 "cards": {
  "person-mira.json": {
   "conversation_id": "person:mira",
   "title": "Mira (person card)",
   "session_date": "2023-06-12",
   "memories": [
    {
     "topic": "Mira",
     "person": "Mira",
     "aspect": "life",
     "summary": "Mira moved to Lisbon and started a pottery class (8 May 2023), then baked a cake for her sister (12 June 2023).",
     "excerpt": "I moved to Lisbon. [...] I started a pottery class. [...] I baked a dairy-free chocolate cake for my sister.",
     "source_line_ids": [
      "D1:1",
      "D1:2",
      "D2:1"
     ],
     "memories_used": [
      "M1",
      "M2",
      "M3"
     ],
     "session_dates": [
      "2023-05-08",
      "2023-06-12"
     ],
     "sessions": [
      "s1@2023-05-08",
      "s2@2023-06-12"
     ],
     "source_session_date": "2023-06-12",
     "first_session_date": "2023-05-08",
     "salience": 0.5,
     "valence": 0.0,
     "emotions": [],
     "context_tags": [
      "person-card"
     ]
    }
   ],
   "rejected": [],
   "calls": [
    {}
   ],
   "writer": {
    "model_cmd": "python3 fake_model.py",
    "claude_version": null,
    "writer_commit": null,
    "writer_dirty": false,
    "prompt_file": "prompt-people.md",
    "prompt_sha256": "f64da5013c2deb0b27a3201ba91df832b1512f8025945e446c65322caa1dc093",
    "env_passed": [
     "HOME",
     "LANG",
     "LOGNAME",
     "PATH",
     "TERM",
     "TMPDIR",
     "USER"
    ],
    "seconds": 0.0,
    "timezone": "as exported",
    "memories_read": 3,
    "cards_updated": 0
   }
  }
 }
}
''')


def old_output(tmp):
    """Write the stored v1.4.0 files into notes/ and cards/ folders under tmp; return both paths."""
    paths = []
    for kind in ("notes", "cards"):
        folder = os.path.join(tmp, kind)
        os.makedirs(folder)
        for name, doc in V140_OUTPUT[kind].items():
            write_json(os.path.join(folder, name), doc)
        paths.append(folder)
    return paths


class OldOutputStillReads(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        tmp = cls.tmp.name
        cls.export = os.path.join(tmp, "conversations.json")
        write_json(cls.export, MIRA_EXPORT)
        cls.notes, cls.cards = old_output(tmp)

    def test_the_old_writers_made_what_is_expected(self):
        self.assertEqual(sorted(os.listdir(self.notes)), ["s1.json", "s2.json"])
        self.assertEqual(len(read_json(os.path.join(self.notes, "s1.json"))["memories"]), 2)
        self.assertEqual(os.listdir(self.cards), ["person-mira.json"])

    def test_old_file_counts_as_finished(self):
        self.assertIs(write_notes.finished(os.path.join(self.notes, "s1.json")), True)

    def test_people_writer_reads_old_files(self):
        self.assertEqual(len(write_people.load_memories(self.notes)), 3)

    def test_people_writer_makes_a_card_from_old_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            res = write_people.write_people(self.export, self.notes, os.path.join(tmp, "out"),
                                            fake_model(os.path.join(tmp, "m.py"), MIRA_CARD))
            self.assertEqual(list(res["people"]), ["mira"])
            self.assertEqual(res["memories_read"], 3)

    def test_nightly_reads_old_cards(self):
        cards = nightly.load_person_cards(self.cards)
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["id"], "mira")
        self.assertEqual(cards[0]["name"], "Mira")

    def test_nightly_cli_finds_a_fact_for_the_old_card(self):
        with tempfile.TemporaryDirectory() as tmp:
            export, out = os.path.join(tmp, "conversations.json"), os.path.join(tmp, "found")
            write_json(export, [{"uuid": "c1", "name": "Chat one", "created_at": "2026-10-01T09:00:00Z", "chat_messages": [
                {"uuid": "m1", "sender": "human", "text": "Mira moved to Lisbon in May.",
                 "created_at": "2026-10-01T10:00:00Z"}]}])
            canned = os.path.join(tmp, "canned.json")
            write_json(canned, [{"fact": "Moved to Lisbon.", "quote": "Mira moved to Lisbon", "message_id": "m1"}])
            fake = os.path.join(tmp, "fake.py")
            with open(fake, "w", encoding="utf-8") as f:
                f.write(FAKE_FACTS % canned)
            argv = ["--export", export, "--cards", self.cards, "--out", out, "--model-cmd",
                    "%s %s" % (sys.executable, fake)]
            buf, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
                rc = nightly.main(argv)
            self.assertEqual(rc, 0, buf.getvalue() + err.getvalue())
            self.assertTrue(os.path.isfile(os.path.join(out, "status.json")))
            facts = read_json(os.path.join(out, "facts", "mira.json"))
            self.assertEqual([f["fact"] for f in facts], ["Moved to Lisbon."])


RETIRED_WORDS = re.compile(r"(?i)\b(?:pen|hand)\b")    # journal is the right word for the AI's own record
ANTECHAMBER_URL = "https://github.com/SolanceLab/antechamber"
FILES = ["README.md", "engine/README.md", "docs/how-it-works.md", "docs/how-it-works.svg", "docs/limits.md",
         "CONTRACT.md", "plugin/skills/scriptorium/SKILL.md", "plugin/skills/journal-writing/SKILL.md",
         "plugin/.claude-plugin/plugin.json", "plugin/.codex-plugin/plugin.json", ".claude-plugin/marketplace.json"]
FALLBACK = ("If the server's tool list has no `write_notes`, the server is older than this skill: call "
            "`write_memories` with the same arguments; it writes the same files.")
OLD_WORD = re.compile(r"(?i)\bmemor(?:y|ies)\b")
ALLOWED = [r"write_memories", r"MEMORY_(?:ENTRY|WRITER)_[A-Z_]+", r"`memories\[\][^`]*`", r"`memories_used`",
           r"`memories`", r'"memories"', r"--memories"]
COMPAT = "The `memories` field holds notes; it keeps its old name so files written before stay readable."


def text_of(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def leftover(text):
    for pattern in ALLOWED:
        text = re.sub(pattern, "", text)
    return text


def descriptions(node):
    """Every description string in a tool entry, however deep. Property names and required lists are not read."""
    if isinstance(node, dict):
        out = [v for k, v in node.items() if k == "description" and isinstance(v, str)]
        for k, v in node.items():
            if k != "description":
                out += descriptions(v)
        return out
    if isinstance(node, list):
        return [d for v in node for d in descriptions(v)]
    return []


class WordsSayNotes(unittest.TestCase):
    def test_no_memory_word_for_script_output(self):
        for rel in FILES:
            with self.subTest(file=rel):
                for n, line in enumerate(text_of(rel).splitlines(), 1):
                    self.assertIsNone(OLD_WORD.search(leftover(line)), "%s:%d %s" % (rel, n, line.strip()[:160]))

    def test_tool_descriptions_say_notes(self):
        for tool in server.TOOLS:
            if tool["name"] == "write_memories":
                continue
            for d in descriptions(tool):
                with self.subTest(tool=tool["name"]):
                    self.assertIsNone(OLD_WORD.search(leftover(d)), d)

    def test_compat_keys_stay_in_the_schemas(self):
        people = next(t for t in server.TOOLS if t["name"] == "write_people")
        self.assertIn("memories", people["inputSchema"]["properties"])
        self.assertIn("memories", people["inputSchema"]["required"])

    def test_compat_field_is_explained(self):
        for rel in ("CONTRACT.md", "docs/how-it-works.md"):
            with self.subTest(file=rel):
                self.assertIn(COMPAT, text_of(rel))

    def test_opening_explains_who_writes_and_who_finds(self):
        readme = text_of("README.md")
        opening = readme.split("\n\n", 3)[2]    # title, maker line, then the opening sentence
        self.assertEqual(opening, f"Scriptorium keeps your AI's journal and writes chat notes, and [Antechamber]({ANTECHAMBER_URL}) "
                                  "finds notes and chat lines without writing anything.")
        self.assertIn(f"[Antechamber]({ANTECHAMBER_URL})", text_of("docs/how-it-works.md"))
        self.assertIn("Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)", readme)

    def test_readme_prose_uses_current_terms(self):
        readme = text_of("README.md")
        prose = re.sub(r"```.*?```|`[^`]*`|(?<=\])\([^)]*\)", "", readme, flags=re.S)
        self.assertIsNone(RETIRED_WORDS.search(prose))

    def test_new_name_leads_where_tools_are_taught(self):
        readme = text_of("README.md").splitlines()
        self.assertTrue(any(ln.startswith("| `write_notes`") for ln in readme))
        self.assertFalse(any(ln.startswith("| `write_memories`") for ln in readme))
        skill = text_of("plugin/skills/scriptorium/SKILL.md").splitlines()
        self.assertTrue(any(ln.startswith("- ") and "**`write_notes`**" in ln for ln in skill))
        self.assertFalse(any("**`write_memories`**" in ln for ln in skill))
        how = text_of("docs/how-it-works.md")
        self.assertIn("python3 engine/write_notes.py --export", how)
        self.assertNotIn("python3 engine/write_memories.py", how)
        rows = [ln for ln in text_of("CONTRACT.md").splitlines() if ln.startswith("| `write_")]
        order = [next(i for i, ln in enumerate(rows) if ln.startswith("| `%s`" % name))
                 for name in ("write_notes", "write_memories")]
        self.assertLess(order[0], order[1])

    def test_journal_means_the_ais_own_writing_only(self):
        readme = text_of("README.md")
        self.assertIn("A journal is what your AI writes in its own words", readme)
        for line in readme.splitlines():
            if line.startswith(("| `write_notes`", "| `write_people`", "**Chat notes.**")):
                self.assertNotRegex(line, r"(?i)\bjournal")
        for rel in ("docs/how-it-works.md", "docs/limits.md"):
            for line in text_of(rel).splitlines():
                if re.search(r"(?i)\bnotes?\b", line) and "journal" in line.lower():
                    self.assertRegex(line, r"(?i)not|never|Journal\.", line)

    def test_skill_falls_back_on_an_older_server(self):
        self.assertIn(FALLBACK, text_of("plugin/skills/scriptorium/SKILL.md"))

    def test_skill_front_matter_is_plain_yaml(self):
        skills = os.path.join(ROOT, "plugin", "skills")
        found = 0
        for name in sorted(os.listdir(skills)):
            path = os.path.join("plugin", "skills", name, "SKILL.md")
            if not os.path.isfile(os.path.join(ROOT, path)):
                continue
            found += 1
            with self.subTest(file=path):
                lines = text_of(path).splitlines()
                self.assertEqual(lines[0], "---")
                end = lines.index("---", 1)
                for ln in lines[1:end]:
                    if ln.startswith((" ", "\t")):
                        continue            # a continuation line of a folded value
                    key, sep, value = ln.partition(": ")
                    self.assertTrue(sep or ln.endswith(":"), ln)
                    value = value.strip()
                    if value and value[0] not in "\"'>|[{":
                        self.assertNotIn(": ", value, ln)
                        self.assertNotIn(" #", value, ln)
        self.assertGreaterEqual(found, 2)

    def test_key_rules_match_the_server(self):
        readme = text_of("README.md")
        for stale in ("Each key belongs to one writer", "reads and writes only its own pen",
                      "With a single agent and no setup"):
            self.assertNotIn(stale, readme)
        for line in ("A key grants write access to every agent it is bound to",
                     "a key bound to no agent grants access to extraction tools only",
                     "docs/agents.md", "SCRIPTORIUM_AGENTS"):
            self.assertIn(line, readme)

    def test_documented_binding_opens_the_pen_for_one_agent(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SCRIPTORIUM_MCP_KEY"] = KEY
            try:
                server.configure(os.path.realpath(tmp), KEY, {"SCRIPTORIUM_MCP_KEY": "Mira"})
                self.assertEqual(server.Config.keys[KEY], ("Mira",))
            finally:
                os.environ.pop("SCRIPTORIUM_MCP_KEY", None)

    def test_quick_start_block_parses_in_bash_and_zsh(self):
        readme = text_of("README.md")
        start = readme.index("## Quick start")
        block = re.search(r"```bash\n(git clone .*?)```", readme[start:], re.S).group(1)
        for line in ('mkdir -p "$HOME/scriptorium"', "read -rs SCRIPTORIUM_MCP_KEY && export SCRIPTORIUM_MCP_KEY"):
            self.assertIn(line, block)
        self.assertIn("SCRIPTORIUM_AGENTS='{\"SCRIPTORIUM_MCP_KEY\":", block)
        shells = [s for s in ("bash", "zsh") if shutil.which(s)]
        self.assertTrue(shells, "no bash or zsh to parse the block with")
        for sh in shells:
            with self.subTest(shell=sh):
                proc = subprocess.run([sh, "-n"], input=block, capture_output=True, text=True)
                self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_entry_store_claims_match_the_code(self):
        readme = text_of("README.md")
        self.assertNotIn("Nothing from your chats is ever written into it", readme)
        self.assertNotIn("no one else writes in it", readme)
        self.assertIn("The extraction tools reject paths inside the entry store", readme)
        for rel in ("README.md", "docs/how-it-works.md", "docs/how-it-works.svg"):
            self.assertNotIn("no one else writes", text_of(rel))
            self.assertNotIn("nothing from your chats", text_of(rel).lower())

    def test_drawing_and_alt_use_current_terms(self):
        readme = text_of("README.md")
        self.assertNotIn("to redo", readme.lower())
        alt = re.search(r'<img[^>]*src="docs/how-it-works\.svg"[^>]*alt="([^"]*)"', readme).group(1)
        self.assertIn("writes journal entries", alt)
        self.assertIn("checks excerpts against their source lines", alt)
        self.assertIsNone(RETIRED_WORDS.search(alt))
        svg = ET.parse(os.path.join(ROOT, "docs", "how-it-works.svg")).getroot()
        drawing = "".join(svg.itertext())
        self.assertIn("Your AI's journal", drawing)
        self.assertIn("Antechamber", drawing)
        self.assertIsNone(RETIRED_WORDS.search(drawing))
        links = svg.findall(".//{http://www.w3.org/2000/svg}a")
        self.assertTrue(links)
        for link in links:
            self.assertEqual(link.get("href"), ANTECHAMBER_URL)
        self.assertIsNone(re.search(r"(?i)memor", leftover(drawing)))


if __name__ == "__main__":
    unittest.main()
