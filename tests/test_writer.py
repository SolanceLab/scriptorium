# Scriptorium — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""The writer keeps only memories its lines prove. Run: python3 -m unittest discover -s engine/tests"""
from contextlib import redirect_stderr, redirect_stdout
import hashlib
from http.client import BadStatusLine, IncompleteRead
import io
import json
import os
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

HERE = os.path.dirname(os.path.abspath(__file__))
# The module keeps the writers in engine/writer; the public copy puts them in engine/ beside tests/.
for _d in (os.path.join(HERE, "..", "writer"), os.path.join(HERE, "..", "engine")):
    if os.path.isdir(_d):
        sys.path.insert(0, _d)
import write_memories as w  # noqa: E402
import write_people as people  # noqa: E402

GOOD = {"topic": "Creative", "keynote": "D1:1 D1:3", "source_line_ids": ["D1:1", "D1:3"],
        "source_session_date": "14 March, 2022",
        "excerpt": "I finally signed up for the pottery class [...] Thursdays at seven.",
        "summary": "Priya told Tom she signed up for the Thursday pottery class.", "emotions": ["proud"],
        "salience": 0.7, "valence": 0.6, "context_tags": ["new-hobby"]}
CONV = {
    "uuid": "conv-1", "name": "3:20 pm on 14 March, 2022", "created_at": "2022-03-14T15:20:00Z",
    "chat_messages": [
        {"uuid": "D1:1", "sender": "human", "created_at": "2022-03-14T15:20:00Z",
         "text": "Priya: I finally signed up for the pottery class at the community centre."},
        {"uuid": "D1:2", "sender": "assistant", "created_at": "2022-03-14T15:21:00Z",
         "content": [{"type": "text", "text": "Tom: No way, the Thursday one?"}]},
        {"uuid": "D1:3", "sender": "human", "created_at": "2022-03-14T15:22:00Z",
         "text": "Priya: Yes, Thursdays at seven."},
    ],
}


def _load(p):
    with open(p) as f:
        return json.load(f)


def _dump(obj, p):
    with open(p, "w") as f:
        json.dump(obj, f)


def by_id():
    _, _, lines = w.claude_lines(CONV)
    return {lid: text for lid, _, text, _ in lines}


class Lines(unittest.TestCase):
    def test_reads_text_and_content_lists(self):
        cid, title, lines = w.claude_lines(CONV)
        self.assertEqual(cid, "conv-1")
        self.assertEqual([l[0] for l in lines], ["D1:1", "D1:2", "D1:3"])
        self.assertEqual(lines[1][1], "assistant")
        self.assertIn("Thursday one", lines[1][2])

    def test_prompt_carries_ids_date_and_speakers(self):
        _, title, lines = w.claude_lines(CONV)
        p = w.build_prompt("TEMPLATE", title, "2022-03-14", lines)
        self.assertIn("[D1:3] user: Priya: Yes, Thursdays at seven.", p)
        self.assertIn("Session date: 2022-03-14", p)


class Checks(unittest.TestCase):
    def test_keeps_a_proven_memory_and_sets_the_session_date(self):
        clean, why = w.check_thread(dict(GOOD), by_id(), "2022-03-14")
        self.assertIsNone(why)
        self.assertEqual(clean["source_session_date"], "2022-03-14")

    def test_rejects_an_invented_excerpt(self):
        bad = dict(GOOD, excerpt="Priya has been doing pottery for ten years.")
        clean, why = w.check_thread(bad, by_id(), "2022-03-14")
        self.assertIsNone(clean)
        self.assertIn("word for word", why)

    def test_rejects_an_excerpt_taken_from_a_line_it_does_not_cite(self):
        bad = dict(GOOD, source_line_ids=["D1:1"], excerpt="Yes, Thursdays at seven.")   # that is D1:3
        clean, why = w.check_thread(bad, by_id(), "2022-03-14")
        self.assertIsNone(clean)

    def test_rejects_an_excerpt_stitched_across_two_lines(self):
        # each line alone is true; joined across the line break they say something nobody said
        lines = {"L1": "I quit the job I loved.", "L2": "Not really, I am staying until spring."}
        bad = dict(GOOD, source_line_ids=["L1", "L2"], excerpt="I loved. Not really")
        clean, why = w.check_thread(bad, lines, "2022-03-14")
        self.assertIsNone(clean)
        self.assertIn("one of its lines", why)

    def test_rejects_an_excerpt_with_no_words_of_its_own(self):
        for empty in ("[...]", " [...] [...] ", "Yes"):
            clean, why = w.check_thread(dict(GOOD, excerpt=empty), by_id(), "2022-03-14")
            self.assertIsNone(clean, empty)

    def test_rejects_a_line_id_not_in_the_session(self):
        clean, why = w.check_thread(dict(GOOD, source_line_ids=["D1:1", "D9:9"]), by_id(), "2022-03-14")
        self.assertIsNone(clean)
        self.assertIn("D9:9", why)

    def test_rejects_missing_sources(self):
        clean, why = w.check_thread(dict(GOOD, source_line_ids=[]), by_id(), "2022-03-14")
        self.assertIsNone(clean)

    def test_off_list_topic_becomes_personal_and_numbers_are_clamped(self):
        clean, _ = w.check_thread(dict(GOOD, topic="Pottery", salience=3, valence=-9), by_id(), "2022-03-14")
        self.assertEqual(clean["topic"], "Personal")
        self.assertEqual((clean["salience"], clean["valence"]), (1.0, -1.0))

    def test_parse_answer_handles_fences_and_rejects_prose(self):
        self.assertEqual(w.parse_answer("```json\n[{\"a\": 1}]\n```"), [{"a": 1}])
        with self.assertRaises(ValueError):
            w.parse_answer("Sorry, I cannot do that.")


class EndToEnd(unittest.TestCase):
    def _fake(self, tmp, answer):
        path = os.path.join(tmp, "fake_model.py")
        with open(path, "w") as f:
            f.write("import sys\nsys.stdin.read()\nprint(" + repr(answer) + ")\n")
        return f"{sys.executable} {path}"

    def test_one_file_per_conversation_with_kept_and_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            export = os.path.join(tmp, "conversations.json")
            _dump([CONV], export)
            answer = json.dumps([GOOD, dict(GOOD, excerpt="Tom owns a pottery studio.")])
            out = os.path.join(tmp, "out")
            rc = w.main(["--export", export, "--out", out, "--model-cmd", self._fake(tmp, answer)])
            self.assertEqual(rc, 0)
            res = _load(os.path.join(out, "conv-1.json"))
            self.assertEqual(len(res["memories"]), 1)
            self.assertEqual(len(res["rejected"]), 1)
            self.assertEqual(res["memories"][0]["source_line_ids"], ["D1:1", "D1:3"])

    def test_a_missing_model_program_is_an_error_not_a_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            export = os.path.join(tmp, "conversations.json")
            _dump([CONV], export)
            rc = w.main(["--export", export, "--out", os.path.join(tmp, "out"),
                         "--model-cmd", "no-such-model-program-xyz"])
            self.assertEqual(rc, 1)

    def test_a_relative_custom_command_still_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.mkdir(os.path.join(tmp, "models"))
            with open(os.path.join(tmp, "models", "echo.py"), "w") as f:
                f.write("import sys\nsys.stdin.read()\nprint('[]')\n")
            here = os.getcwd()
            os.chdir(tmp)
            try:
                self.assertEqual(w.call_model(f"{sys.executable} models/echo.py", "x", 30).strip(), "[]")
                shutil.copy(os.path.join("models", "echo.py"), "bare.py")
                self.assertEqual(w.call_model(f"{sys.executable} bare.py", "x", 30).strip(), "[]")
            finally:
                os.chdir(here)

    def test_a_file_in_the_current_folder_never_stands_in_for_the_program(self):
        with tempfile.TemporaryDirectory() as tmp:
            bait = os.path.join(tmp, "python3")
            with open(bait, "w") as f:
                f.write("#!/bin/sh\necho HIJACKED\n")
            os.chmod(bait, 0o755)
            here = os.getcwd()
            os.chdir(tmp)
            try:
                self.assertNotEqual(w._absolute_args(["python3", "x"])[0], bait)
            finally:
                os.chdir(here)

    def test_the_model_runs_in_an_empty_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "peek.py")
            with open(path, "w") as f:
                f.write("import os, sys, json\nsys.stdin.read()\nprint(json.dumps(os.listdir('.')))\n")
            self.assertEqual(w.call_model(f"{sys.executable} {path}", "x", 30).strip(), "[]")

    def test_default_command_has_no_tools_and_no_settings(self):
        import shlex
        args = shlex.split(w.DEFAULT_MODEL_CMD)
        self.assertEqual(args[args.index("--tools") + 1], "")
        self.assertEqual(args[args.index("--setting-sources") + 1], "")

    def test_an_overlong_summary_is_rejected(self):
        clean, why = w.check_thread(dict(GOOD, summary="x" * 2000), by_id(), "2022-03-14")
        self.assertIsNone(clean)

    def test_a_garbled_answer_is_an_error_not_a_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            export = os.path.join(tmp, "conversations.json")
            _dump([CONV], export)
            out = os.path.join(tmp, "out")
            rc = w.main(["--export", export, "--out", out, "--model-cmd", self._fake(tmp, "not json")])
            self.assertEqual(rc, 1)
            res = _load(os.path.join(out, "conv-1.json"))
            self.assertIn("no JSON array", res["error"])
            self.assertEqual(res["memories"], [])



class ApiBilling(unittest.TestCase):
    KEY = "fake-anthropic-test-key"
    MODEL = "claude-sonnet-5-5"
    SYSTEM = "You write notes from one conversation. Output only valid JSON."
    NOTICE = ("ANTHROPIC_API_KEY is set, but the default runs through your Claude login; "
              "add --api to bill the API key instead.\n")

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        export = os.path.join(temp.name, "conversations.json")
        _dump([CONV], export)
        self.out = os.path.join(temp.name, "out")
        self.args = ["--export", export, "--out", self.out, "--prompt", "public"]
        self.stdout, self.stderr = io.StringIO(), io.StringIO()
        for context in (redirect_stdout(self.stdout), redirect_stderr(self.stderr),
                        patch.dict(os.environ, {"ANTHROPIC_API_KEY": self.KEY, "MEMORY_WRITER_CMD": ""})):
            context.__enter__()
            self.addCleanup(context.__exit__, None, None, None)
        del os.environ["MEMORY_WRITER_CMD"]
        http = patch.object(w.request, "urlopen")
        self.http = http.start()
        self.addCleanup(http.stop)

    def response(self, **fields):
        message = {"model": self.MODEL, "usage": {"input_tokens": 1000, "output_tokens": 500},
                   "content": [{"type": "text", "text": json.dumps([GOOD])}], "stop_reason": "end_turn"}
        message.update(fields)
        return io.BytesIO(json.dumps(message).encode())

    def http_error(self, body=None):
        if body is None:
            bound = 4 * (300 + len(self.KEY))
            body = (self.KEY * (bound // len(self.KEY) + 2)).encode()
        return HTTPError("https://api.anthropic.com/v1/messages", 400, "Bad Request", {},
                         io.BytesIO(body))

    def summary(self):
        return json.loads(self.stdout.getvalue().splitlines()[-2])

    def test_missing_key_is_refused_before_opening_the_export(self):
        os.environ["ANTHROPIC_API_KEY"] = ""
        with self.assertRaises(SystemExit) as exc:
            w.main(["--export", "missing.json", "--out", self.out, "--api"])
        self.assertNotEqual(exc.exception.code, 0)
        self.assertIn("--api requires ANTHROPIC_API_KEY", self.stderr.getvalue())
        self.http.assert_not_called()

    def test_custom_command_is_refused(self):
        with self.assertRaises(SystemExit) as exc:
            w.main(self.args + ["--api", "--model-cmd", "custom"])
        self.assertNotEqual(exc.exception.code, 0)
        self.assertIn("--api cannot be used with --model-cmd", self.stderr.getvalue())
        self.http.assert_not_called()

    def test_api_request_and_sonnet_cost(self):
        self.http.return_value = self.response()
        with patch.object(w.subprocess, "run", wraps=w.subprocess.run) as process:
            self.assertEqual(w.main(self.args + ["--api", "--model", self.MODEL, "--timeout", "17"]), 0)
        self.assertTrue(all(call.args[0][0] == "git" for call in process.call_args_list))
        req, = self.http.call_args.args
        self.assertEqual(self.http.call_args.kwargs, {"timeout": 17})
        self.assertEqual(req.full_url, "https://api.anthropic.com/v1/messages")
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(req.get_header("X-api-key"), self.KEY)
        self.assertNotIn("X-api-key", req.headers)
        self.assertEqual(req.get_header("Anthropic-version"), "2026-10-21")
        self.assertEqual(req.get_header("Content-type"), "application/json")
        body = json.loads(req.data)
        self.assertEqual(body["model"], self.MODEL)
        self.assertEqual(body["system"], self.SYSTEM)
        self.assertEqual(body["max_tokens"], 16000)
        message, = body["messages"]
        self.assertEqual(message["role"], "user")
        self.assertIn(CONV["chat_messages"][0]["text"], message["content"])
        self.assertNotIn(self.KEY, message["content"])
        result = _load(os.path.join(self.out, "conv-1.json"))
        call, = result["calls"]
        self.assertEqual(call["total_cost_usd"], 0.007)
        self.assertEqual(call["models"], [self.MODEL])
        self.assertEqual(call["usage"], {"input_tokens": 1000, "output_tokens": 500})
        self.assertGreaterEqual(call["duration_ms"], 0)
        self.assertEqual(self.stderr.getvalue(), "")
        self.assertNotIn(self.KEY, json.dumps(result) + self.stdout.getvalue())

    def test_unknown_model_makes_cost_incomplete(self):
        self.http.return_value = self.response(model="future-model")
        self.assertEqual(w.main(self.args + ["--api", "--model", "future-model"]), 0)
        result = _load(os.path.join(self.out, "conv-1.json"))
        self.assertIsNone(result["calls"][0].get("total_cost_usd"))
        self.assertEqual(self.summary()["cost_usd"], 0.0)
        self.assertTrue(self.summary()["cost_incomplete"])

    def test_api_defaults_to_the_sonnet_model_id(self):
        self.http.return_value = self.response()
        self.assertEqual(w.main(self.args + ["--api"]), 0)
        self.assertEqual(json.loads(self.http.call_args.args[0].data)["model"], self.MODEL)

    def test_other_known_models_use_their_own_rates(self):
        for model, expected in (("claude-haiku-5-5", 0.00035), ("claude-opus-5-5", 0.014)):
            with self.subTest(model=model):
                self.http.return_value = self.response(model=model)
                _, record = w.call_api(model, self.KEY, "prompt", 17)
                self.assertAlmostEqual(record["total_cost_usd"], expected)

    def test_non_json_http_error_only_shows_status(self):
        self.http.side_effect = self.http_error()
        with self.assertRaises(RuntimeError) as exc:
            w.call_api(self.MODEL, self.KEY, "prompt", 17)
        self.assertEqual(str(exc.exception), "HTTP 400")

    def test_json_http_error_only_shows_bounded_redacted_fields(self):
        payload = {"error": {"type": self.KEY + "T" * 310, "message": "x" * 295 + self.KEY},
                   "request_id": "must not appear"}
        self.http.side_effect = self.http_error(json.dumps(payload).encode())
        with self.assertRaises(RuntimeError) as exc:
            w.call_api(self.MODEL, self.KEY, "prompt", 17)
        self.assertEqual(str(exc.exception), "HTTP 400: " + "<redacted>" + "T" * 290 + ": " + "x" * 295 + "<reda")

    def test_http_protocol_errors_are_normalized(self):
        partial = IncompleteRead(self.KEY.encode())
        response = self.response()
        with response, patch.object(response, "read", side_effect=partial):
            for failure in (BadStatusLine(self.KEY), response):
                with self.subTest(failure=type(failure)):
                    self.http.side_effect = [failure]
                    with self.assertRaises(RuntimeError) as exc:
                        w.call_api(self.MODEL, self.KEY, "prompt", 17)
                    self.assertNotIn(self.KEY, str(exc.exception))
        http_error = self.http_error()
        self.http.side_effect = http_error
        with patch.object(http_error, "read", side_effect=partial):
            with self.assertRaisesRegex(RuntimeError, "^HTTP 400$"):
                w.call_api(self.MODEL, self.KEY, "prompt", 17)

    def test_refusal_raises(self):
        self.http.return_value = self.response(stop_reason="refusal")
        with self.assertRaisesRegex(RuntimeError, "Anthropic API refused"):
            w.call_api(self.MODEL, self.KEY, "prompt", 17)

    def test_only_completion_stop_reasons_succeed(self):
        for reason in ("pause_turn", "model_context_window_exceeded", None):
            with self.subTest(reason=reason):
                self.http.return_value = self.response(stop_reason=reason)
                with self.assertRaisesRegex(RuntimeError, "non-completion") as exc:
                    w.call_api(self.MODEL, self.KEY, "prompt", 17)
                self.assertEqual(exc.exception.record["total_cost_usd"], 0.007)
        self.http.return_value = self.response(stop_reason="stop_sequence")
        answer, _ = w.call_api(self.MODEL, self.KEY, "prompt", 17)
        self.assertEqual(json.loads(answer), [GOOD])

    def test_billed_failures_count_across_regular_and_date_retries(self):
        wrong_date = dict(GOOD, summary="Priya signed up on Friday 14 March 2022.")
        date_response = self.response(content=[{"type": "text", "text": json.dumps([GOOD, wrong_date])}])
        self.http.side_effect = [self.response(stop_reason="max_tokens"), date_response,
                                 self.response(stop_reason="refusal")]
        self.assertEqual(w.main(self.args + ["--api"]), 0)
        result = _load(os.path.join(self.out, "conv-1.json"))
        self.assertEqual([call["total_cost_usd"] for call in result["calls"]], [0.007] * 3)
        self.assertEqual(result["calls"][-1]["retry"], "date")
        self.assertEqual(self.summary()["cost_usd"], 0.021)
        self.assertNotIn("cost_incomplete", self.summary())

    def test_timeout_marks_cost_incomplete_even_after_successful_retry(self):
        self.http.side_effect = [TimeoutError(self.KEY), self.response()]
        self.assertEqual(w.main(self.args + ["--api"]), 0)
        self.assertEqual(self.summary()["cost_usd"], 0.007)
        self.assertTrue(self.summary()["cost_incomplete"])
        self.assertIn("cost may be incomplete", self.stdout.getvalue())

    def test_truncated_or_malformed_messages_raise(self):
        for fields, message in (({"stop_reason": "max_tokens"}, "reached the token limit"),
                                ({"content": None}, "returned an invalid message")):
            with self.subTest(fields=fields):
                self.http.return_value = self.response(**fields)
                with self.assertRaisesRegex(RuntimeError, message):
                    w.call_api(self.MODEL, self.KEY, "prompt", 17)

    def test_key_in_prompt_is_refused_before_sending(self):
        with self.assertRaises(RuntimeError) as exc:
            w.call_api(self.MODEL, self.KEY, "prompt " + self.KEY, 17)
        self.assertNotIn(self.KEY, str(exc.exception))
        self.http.assert_not_called()

    def test_http_errors_are_retried_and_stay_redacted_in_cli_output(self):
        self.http.side_effect = [self.http_error(), self.http_error()]
        self.assertEqual(w.main(self.args + ["--api", "--model", self.MODEL]), 1)
        self.assertEqual(self.http.call_count, 2)
        result = _load(os.path.join(self.out, "conv-1.json"))
        self.assertIn("HTTP 400", result["error"])
        self.assertEqual(self.summary()["cost_usd"], 0.0)
        self.assertTrue(self.summary()["cost_incomplete"])
        self.assertNotIn(self.KEY, json.dumps(result) + self.stdout.getvalue() + self.stderr.getvalue())

    def test_echoed_key_is_redacted_from_answer_and_metadata(self):
        self.http.return_value = self.response(
            model=self.KEY, usage={"nested": [self.KEY]},
            content=[{"type": "text", "text": self.KEY}])
        answer, record = w.call_api(self.MODEL, self.KEY, "prompt", 17)
        self.assertNotIn(self.KEY, answer + json.dumps(record))

    def test_default_warns_once_and_keeps_the_key_out_of_the_environment(self):
        _dump([CONV, dict(CONV, uuid="conv-2")], self.args[1])
        completed = SimpleNamespace(returncode=0, stdout=json.dumps([GOOD]))
        with patch.object(w.subprocess, "run", return_value=completed) as process:
            self.assertEqual(w.main(self.args), 0)
        calls = [call for call in process.call_args_list if "input" in call.kwargs]
        self.assertEqual(len(calls), 2)
        for call in calls:
            args = call.args[0]
            self.assertEqual(args[args.index("--system-prompt") + 1], self.SYSTEM)
            self.assertNotIn("ANTHROPIC_API_KEY", call.kwargs["env"])
        self.assertEqual(self.stderr.getvalue(), self.NOTICE)
        self.http.assert_not_called()

    def test_custom_command_does_not_warn(self):
        with patch.object(w, "call_model", return_value=json.dumps([GOOD])):
            self.assertEqual(w.main(self.args + ["--model-cmd", "custom"]), 0)
        self.assertEqual(self.stderr.getvalue(), "")
        self.http.assert_not_called()


class QuoteRepair(unittest.TestCase):
    FIRST = "We chose the blue bowls for dinner"
    SECOND = "The green plates will arrive after lunch"
    THIRD = "We ordered twelve glass cups for dessert"

    def write(self, excerpt, bodies=None, ids=None, summary=None, added=None, max_chars=w.MAX_PIECE_CHARS,
              retry_answer=None):
        bodies = bodies or [self.FIRST, self.SECOND, self.THIRD]
        conv = dict(CONV, chat_messages=[
            {"uuid": f"L{i}", "text": body, "sender": "user"} for i, body in enumerate(bodies, 1)
        ])
        note = dict(GOOD, excerpt=excerpt, source_line_ids=ids or ["L1"], summary=summary or GOOD["summary"],
                    added_line_ids=added or ["bogus"])
        with patch.object(w, "call_model", side_effect=lambda _cmd, prompt, _timeout: json.dumps(
            [retry_answer] if retry_answer and "previous answer had these date errors" in prompt else [note]
        )):
            return w.write_conversation(conv, "template", "fake", 60, max_chars=max_chars)

    def test_repairs_in_transcript_order_and_adds_each_id_once(self):
        body = "The green plates will arrive\n after lunch. " + self.THIRD
        out = self.write(f"{self.SECOND} [...] {self.THIRD} [...] {self.FIRST}",
                         [body, self.FIRST], ids=["L2"])
        note, = out["memories"]
        self.assertEqual(note["source_line_ids"], ["L1", "L2"])
        self.assertEqual(note["added_line_ids"], ["L1"])
        self.assertEqual(out["rejected"], [])

    def test_only_the_writer_can_supply_added_ids(self):
        note, = self.write(self.FIRST)["memories"]
        self.assertNotIn("added_line_ids", note)
        reject, = self.write("This invented fragment never appeared here")["rejected"]
        self.assertNotIn("added_line_ids", reject["thread"])

    def test_rejects_ambiguous_short_stitched_missing_and_out_of_order_fragments(self):
        cases = [
            (self.SECOND, [self.FIRST, self.SECOND, self.SECOND]),
            ("green plates will arrive after", None),
            ("plates will arrive after lunch We ordered twelve", None),
            ("These words are absent from the chat", None),
            (f"{self.SECOND} [...] {self.FIRST}", None),
            ("The green plates will arrive after lunch", [self.FIRST, self.SECOND + "es"]),
            ("green plates will arrive after lunch", [self.FIRST, "evergreen plates will arrive after lunch"]),
            (self.SECOND, [self.FIRST, self.SECOND + " " + self.THIRD, self.SECOND]),
            ("we don't think it's", [self.FIRST, "we don't think it's ready yet"]),    # 4 words, 6 word-pieces
            ("2026-11-21 18:06:20", [self.FIRST, "logged at 2026-11-21 18:06:20 today"]),
        ]
        for excerpt, bodies in cases:
            with self.subTest(excerpt=excerpt, bodies=bodies):
                out = self.write(excerpt, bodies)
                self.assertEqual(out["memories"], [])
                self.assertIn("word for word", out["rejected"][0]["reason"])

    def test_repeated_occurrences_in_one_message_count_as_one_match(self):
        note, = self.write(self.SECOND, [self.FIRST, self.SECOND + ". " + self.SECOND])["memories"]
        self.assertEqual(note["added_line_ids"], ["L2"])

    def test_six_words_are_enough_and_multiple_repairs_keep_order(self):
        note, = self.write(f"{self.FIRST} [...] green plates will arrive after lunch [...] {self.THIRD}")["memories"]
        self.assertEqual(note["source_line_ids"], ["L1", "L2", "L3"])
        self.assertEqual(note["added_line_ids"], ["L2", "L3"])

    def test_other_model_call_cannot_repair_a_quote(self):
        out = self.write(self.SECOND, max_chars=70)
        self.assertGreater(len(out["pieces"]), 1)
        self.assertEqual(out["memories"], [])
        self.assertIn("word for word", out["rejected"][0]["reason"])

    def test_repair_requires_unambiguous_line_ids(self):
        note = dict(GOOD, excerpt=self.SECOND, source_line_ids=["L1"])
        for duplicate in ("L1", "L2"):
            with self.subTest(duplicate=duplicate):
                lines = [
                    ("L1", "user", self.FIRST, None),
                    ("L2", "assistant", self.SECOND, None),
                    (duplicate, "user", self.THIRD, None),
                ]
                notes, rejected, _, _ = w._write_piece(
                    "Example", "2022-03-14", lines, "template",
                    lambda *_: (json.dumps([note]), {}), retries=0,
                )
                self.assertEqual(notes, [])
                self.assertIn("word for word", rejected[0]["reason"])

    def test_repaired_line_neither_introduces_nor_excuses_a_date_error(self):
        summary = "They ordered the dishes on 4 March 2022."
        out = self.write(self.SECOND, [self.FIRST, self.SECOND + ". I ordered them last Friday."], summary=summary)
        self.assertEqual(len(out["memories"]), 1)
        self.assertEqual(len(out["calls"]), 1)
        out = self.write(self.SECOND, [self.FIRST + ". I ordered them last Friday.",
                                      self.SECOND + ". The shop opened on 4 March 2022."], summary=summary)
        self.assertEqual(out["memories"], [])
        self.assertIn("word for word", out["rejected"][0]["reason"])
        self.assertEqual(len(out["calls"]), 1)

    def test_existing_date_retry_can_return_a_repaired_note(self):
        wrong = "They ordered the dishes on 4 March 2022."
        right = "They ordered the dishes on 11 March 2022."
        retry = dict(GOOD, excerpt=self.SECOND, source_line_ids=["L1"], summary=right)
        out = self.write(self.FIRST, [self.FIRST + ". I ordered them last Friday.", self.SECOND],
                         summary=wrong, retry_answer=retry)
        note, = out["memories"]
        self.assertEqual(note["summary"], right)
        self.assertEqual(note["added_line_ids"], ["L2"])
        self.assertEqual(out["calls"][1]["retry"], "date")

    def test_repair_checks_the_excerpt_without_establishing_summary_support(self):
        summary = "They won a national award for their dishes."
        note, = self.write(self.SECOND, summary=summary)["memories"]
        self.assertEqual(note["summary"], summary)
        self.assertEqual(note["added_line_ids"], ["L2"])

    def test_people_reader_loads_old_and_repaired_notes_and_cards_do_not_repair(self):
        repaired = self.write(self.SECOND)
        old = dict(conversation_id="old", memories=[
            dict(repaired["memories"][0], excerpt=self.FIRST, source_line_ids=["L1"])
        ])
        del old["memories"][0]["added_line_ids"]
        with tempfile.TemporaryDirectory() as tmp:
            _dump(repaired, os.path.join(tmp, "repaired.json"))
            _dump(old, os.path.join(tmp, "old.json"))
            records = {record["cid"]: record for record in people.load_memories(tmp)}
        self.assertEqual(set(records), {"conv-1", "old"})
        by_id = {"L1": self.FIRST, "L2": self.SECOND}
        when = dict.fromkeys(by_id, "2022-03-14")
        for cid, expected in (("conv-1", ["L1", "L2"]), ("old", ["L1"])):
            row = records[cid]
            self.assertEqual(row["lines"], expected)
            self.assertNotIn("added_line_ids", row)
            item = {"person": "Priya", "aspect": "pottery", "memories": [row["mid"]],
                    "summary": "Priya signed up for pottery on 14 March 2022."}
            card, why = people.check_item(item, by_id, when, {row["mid"]: row})
            self.assertIsNone(why)
            self.assertEqual(card["source_line_ids"], expected)
        row = dict(records["old"], excerpt=self.SECOND)
        item["memories"] = [row["mid"]]
        card, why = people.check_item(item, by_id, when, {row["mid"]: row})
        self.assertIsNone(card)
        self.assertIn("word for word", why)


class RunRecord(unittest.TestCase):
    """Benchmark review 17 Oct: pinned model + full provenance, atomic files, honest re-runs, public prompt, clean env."""

    def _export(self, tmp, convs=None):
        export = os.path.join(tmp, "conversations.json")
        _dump(convs or [CONV], export)
        return export

    def _counting_fake(self, tmp, answer):
        """A fake model that also counts its calls, so a test can tell whether a conversation was redone."""
        path = os.path.join(tmp, "fake_count.py")
        log = os.path.join(tmp, "calls.log")
        with open(path, "w") as f:
            f.write("import sys\nsys.stdin.read()\nopen(%r, 'a').write('x')\nprint(%r)\n" % (log, answer))
        return f"{sys.executable} {path}", log

    def test_a_pinned_model_goes_into_the_default_command(self):
        import shlex
        cmd = w.MODEL_CMD_TEMPLATE.format(model="claude-sonnet-5-5")
        args = shlex.split(cmd)
        self.assertEqual(args[args.index("--model") + 1], "claude-sonnet-5-5")
        self.assertEqual(args[args.index("--output-format") + 1], "json")
        self.assertEqual(args[args.index("--tools") + 1], "")

    def test_the_claude_envelope_is_unwrapped_and_recorded(self):
        env = {"type": "result", "is_error": False, "result": "[]", "total_cost_usd": 0.0123,
               "usage": {"input_tokens": 10, "output_tokens": 5}, "duration_ms": 900,
               "modelUsage": {"claude-sonnet-5-5": {"inputTokens": 10}}}
        text, rec = w.unwrap(json.dumps(env))
        self.assertEqual(text, "[]")
        self.assertEqual(rec["models"], ["claude-sonnet-5-5"])
        self.assertEqual(rec["total_cost_usd"], 0.0123)
        self.assertEqual(w.unwrap("[1]"), ("[1]", {}))          # plain output passes through
        with self.assertRaises(RuntimeError):
            w.unwrap(json.dumps(dict(env, is_error=True, result="overloaded")))

    def test_every_file_records_the_command_prompt_sha_and_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            cmd, _ = self._counting_fake(tmp, json.dumps([GOOD]))
            out = os.path.join(tmp, "out")
            with patch.dict(os.environ, {"SCRIPTORIUM_USER_NAME": "", "SCRIPTORIUM_AGENT_NAME": ""}):
                w.main(["--export", self._export(tmp), "--out", out, "--model-cmd", cmd, "--prompt", "public"])
            rec = _load(os.path.join(out, "conv-1.json"))["writer"]
            self.assertEqual(rec["model_cmd"], cmd)
            self.assertEqual(rec["prompt_file"], "prompt-public.md")
            with open(w.PROMPTS["public"], encoding="utf-8") as f:
                instructions = w.render_instructions(f.read(), "public", None, None)
            self.assertEqual(rec["prompt_sha256"], hashlib.sha256(instructions.encode("utf-8")).hexdigest())
            self.assertIn("writer_commit", rec)
            self.assertIn("claude_version", rec)

    def test_names_reach_the_model_from_flags_or_env_and_change_the_fingerprint(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "out")
            args = ["--export", self._export(tmp), "--out", out, "--model-cmd", "fake", "--prompt", "public"]
            digests = []
            for env, flags, labels in (
                ({}, [], ("the user", "the assistant")),
                ({"SCRIPTORIUM_USER_NAME": "Morgan", "SCRIPTORIUM_AGENT_NAME": "Casey"}, [], ("Morgan", "Casey")),
                ({"SCRIPTORIUM_USER_NAME": "Morgan", "SCRIPTORIUM_AGENT_NAME": "Casey"},
                 ["--user-name", "Riley", "--agent-name", "Taylor"], ("Riley", "Taylor")),
                ({"SCRIPTORIUM_AGENT_NAME": "Casey"}, [], ("the user", "Casey")),
            ):
                with (patch.dict(os.environ, env, clear=True),
                      patch.object(w, "call_model", return_value=json.dumps([GOOD])) as model):
                    self.assertEqual(w.main(args + flags), 0)
                prompt = model.call_args.args[1]
                instructions = prompt.split("\n\n---\n\nSession:", 1)[0]
                self.assertIn(f'call the user "{labels[0]}" and the assistant\n"{labels[1]}"', instructions)
                self.assertIn("use that speaker name", instructions)
                self.assertIn("[D1:1] user: Priya:", prompt)
                writer = _load(os.path.join(out, "conv-1.json"))["writer"]
                self.assertEqual(writer["prompt_sha256"], hashlib.sha256(instructions.encode("utf-8")).hexdigest())
                for name in ("Morgan", "Casey", "Riley", "Taylor"):
                    self.assertNotIn(name, json.dumps(writer))
                digests.append(writer["prompt_sha256"])
            self.assertEqual(len(set(digests)), len(digests))

    def test_custom_prompt_path_is_used_verbatim_and_house_can_remain_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "out")
            prompt_path = os.path.join(tmp, "prompt.md")
            template = "Instructions with literal {{USER_NAME}} and {{AGENT_NAME}}."
            with open(prompt_path, "w", encoding="utf-8") as f:
                f.write(template)
            args = ["--export", self._export(tmp), "--out", out, "--model-cmd", "fake",
                    "--user-name", "Morgan", "--agent-name", "Casey"]
            with (patch.dict(w.PROMPTS, {"house": prompt_path}),
                  patch.object(w, "DEFAULT_PROMPT", "house"),
                  patch.object(w, "call_model", return_value=json.dumps([GOOD])) as model):
                self.assertEqual(w.main(args + ["--prompt", prompt_path]), 0)
                self.assertTrue(model.call_args.args[1].startswith(template + "\n\n---"))
                self.assertEqual(w.main(args), 0)
                self.assertIn("Instructions with literal Morgan and Casey.", model.call_args.args[1])
                self.assertIn('use "Morgan" for the user and "Casey" for the assistant', model.call_args.args[1])

    def test_public_prompt_cites_support_and_names_only_by_placeholder(self):
        with open(w.PROMPTS["public"], encoding="utf-8") as f:
            template = f.read()
        self.assertIn("Cite every dialogue line a claim rests on, including the reply that settles it.", template)
        example = json.loads(template.split("```json\n", 1)[1].split("```", 1)[0])
        self.assertEqual(example[0]["source_line_ids"], ["D1:1", "D1:2", "D1:3"])

    def test_skip_existing_redoes_errored_and_empty_files_but_keeps_finished_ones(self):
        two = [CONV, dict(CONV, uuid="conv-2"), dict(CONV, uuid="conv-3")]
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "out")
            os.makedirs(out)
            _dump({"memories": [GOOD], "rejected": []}, os.path.join(out, "conv-1.json"))       # finished
            _dump({"memories": [], "rejected": [], "error": "boom"}, os.path.join(out, "conv-2.json"))  # errored
            _dump({"memories": [], "rejected": []}, os.path.join(out, "conv-3.json"))             # empty
            cmd, log = self._counting_fake(tmp, json.dumps([GOOD]))
            rc = w.main(["--export", self._export(tmp, two), "--out", out, "--model-cmd", cmd, "--skip-existing"])
            self.assertEqual(rc, 0)
            with open(log, encoding="utf-8") as f:
                self.assertEqual(f.read(), "xx")  # conv-2 and conv-3 redone, conv-1 left alone
            self.assertEqual(len(_load(os.path.join(out, "conv-2.json"))["memories"]), 1)

    def test_an_empty_run_is_reported_and_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            cmd, _ = self._counting_fake(tmp, "[]")
            rc = w.main(["--export", self._export(tmp), "--out", os.path.join(tmp, "out"), "--model-cmd", cmd])
            self.assertEqual(rc, 1)

    def test_a_failed_write_leaves_the_old_file_and_no_temp_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "conv-1.json")
            _dump({"memories": [GOOD]}, path)
            with self.assertRaises(TypeError):
                w.write_atomic(path, {"memories": {1, 2}})            # a set cannot be written as JSON
            self.assertEqual(_load(path), {"memories": [GOOD]})
            self.assertEqual(os.listdir(tmp), ["conv-1.json"])

    def test_the_model_sees_only_the_minimal_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "env_model.py")
            with open(path, "w") as f:
                f.write("import os, sys, json\nsys.stdin.read()\nprint(json.dumps(sorted(os.environ)))\n")
            os.environ["HOUSE_SECRET_FOR_TEST"] = "x"
            try:
                seen = json.loads(w.call_model(f"{sys.executable} {path}", "x", 30))
            finally:
                del os.environ["HOUSE_SECRET_FOR_TEST"]
            self.assertNotIn("HOUSE_SECRET_FOR_TEST", seen)
            self.assertTrue(set(seen) <= set(w.ENV_KEEP) | {"__CF_USER_TEXT_ENCODING", "PWD", "SHLVL", "_"})

    def test_the_public_prompt_has_no_house_notice_or_vocabulary(self):
        with open(w.PROMPTS["public"], encoding="utf-8") as f:
            text = f.read()
        for word in ("Solance", "House", "keynote", "Keynote", "All rights " + "reserved"):
            self.assertNotIn(word, text)
        house = None
        if os.path.exists(w.PROMPTS["house"]):
            with open(w.PROMPTS["house"], encoding="utf-8") as f:
                house = f.read()
        for rule in ("Excerpts come from the named source lines", "READ it off the calendar",
                     "Salience tracks structural role", "Output only a valid JSON array"):
            self.assertIn(rule, text)
            if house is not None:          # the House prompt exists only in the House copy
                self.assertIn(rule, house)



class Pieces(unittest.TestCase):
    """Long chats that run over many days are written one day at a time (and a too-long day in parts),
    each piece with its own date; a one-day short conversation is one piece, exactly as before."""

    def L(self, lid, at, n=10):
        return (lid, "user", "x" * n, at)

    def test_one_short_day_is_one_piece(self):
        lines = [self.L("a", "2023-07-15T10:00:00Z"), self.L("b", "2023-07-15T23:00:00Z")]
        self.assertEqual(w.split_pieces(lines, "2023-07-15"), [("2023-07-15", lines)])

    def test_a_new_day_starts_a_new_piece_and_a_line_without_a_date_stays_with_the_one_before(self):
        a, b, c = self.L("a", "2023-07-15T10:00:00Z"), self.L("b", None), self.L("c", "2023-07-17T09:00:00Z")
        self.assertEqual(w.split_pieces([a, b, c], "2023-07-15"), [("2023-07-15", [a, b]), ("2023-07-17", [c])])

    def test_a_too_long_day_is_cut_and_one_huge_message_stands_alone(self):
        lines = [self.L(str(i), "2023-07-15T10:00:00Z", 100) for i in range(5)] + [self.L("big", "2023-07-15T11:00:00Z", 1000)]
        pieces = w.split_pieces(lines, "2023-07-15", max_chars=300)
        self.assertTrue(all(day == "2023-07-15" for day, _ in pieces))
        self.assertEqual(sum(len(p) for _, p in pieces), 6)
        self.assertEqual(pieces[-1][1], [lines[-1]])

    def test_a_time_zone_moves_the_day(self):
        # 23:30 UTC on 14 July is 06:30 on 15 July in Jakarta (UTC+7)
        self.assertEqual(w.local_day("2023-07-14T23:30:00Z"), "2023-07-14")
        self.assertEqual(w.local_day("2023-07-14T23:30:00Z", "Asia/Jakarta"), "2023-07-15")
        self.assertEqual(w.local_day("2023-07-14T23:30:00+00:00", "Asia/Jakarta"), "2023-07-15")
        lines = [self.L("a", "2023-07-14T15:00:00Z"), self.L("b", "2023-07-14T23:30:00Z")]
        self.assertEqual(len(w.split_pieces(lines, "2023-07-14")), 1)                   # one UTC day
        self.assertEqual([d for d, _ in w.split_pieces(lines, "2023-07-14", tz="Asia/Jakarta")],
                         ["2023-07-14", "2023-07-15"])                                    # two Jakarta days

    def test_short_fractions_of_a_second_parse_on_python_3_9(self):
        # Postgres trims trailing zeros (.020 -> .02); Python 3.9 and 3.10 fromisoformat only take 3 or 6 digits
        for s in ("2026-11-16T22:24:42.02+07:00", "2026-11-16T22:24:42.0234+07:00", "2026-11-16T22:24:42.1Z",
                  "2026-11-16T22:24:42.123456789+07:00"):
            t = w.parse_iso(s)
            self.assertEqual((t.hour, t.minute, t.second), (22, 24, 42))
            self.assertIsNotNone(t.tzinfo)
        self.assertEqual(w.parse_iso("2026-11-16T22:24:42.02+07:00").microsecond, 20000)
        self.assertEqual(w.local_day("2026-11-16T22:24:42.02+00:00", "Asia/Jakarta"), "2026-11-17")
        with self.assertRaises(ValueError):
            w.parse_iso("not a time")

    def test_a_one_piece_conversation_takes_the_day_its_messages_carry(self):
        conv = {"uuid": "x", "name": "x", "created_at": "2023-07-14T10:00:00Z", "chat_messages": [
            {"uuid": "D1:1", "sender": "human", "created_at": "2023-07-15T09:00:00Z", "text": "Hello there friend."}]}
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "m.py")
            with open(path, "w") as f:
                f.write("import sys\np = sys.stdin.read()\nprint('[]' if 'Session date: 2023-07-15' in p else 'wrong')\n")
            res = w.write_conversation(conv, "T", f"{sys.executable} {path}", 30)
        self.assertNotIn("error", res)              # 'wrong' would be no JSON array, an error

    def test_a_conversation_with_too_many_pieces_is_refused_before_any_call(self):
        msgs = [{"uuid": f"m{i}", "sender": "human", "created_at": f"2023-{1 + i // 28:02d}-{1 + i % 28:02d}T10:00:00Z",
                 "text": "hello"} for i in range(w.MAX_PIECES + 1)]
        res = w.write_conversation({"uuid": "big", "name": "big", "chat_messages": msgs}, "T", "no-such-program", 30)
        self.assertIn("nothing was sent", res["error"])
        self.assertEqual(res["calls"], [])

    def test_with_skip_existing_the_limit_counts_conversations_written_so_runs_continue(self):
        convs = [dict(CONV, uuid=f"c{i}") for i in range(3)]
        with tempfile.TemporaryDirectory() as tmp:
            export = os.path.join(tmp, "e.json")
            _dump(convs, export)
            fake = os.path.join(tmp, "f.py")
            with open(fake, "w") as f:
                f.write("import sys\nsys.stdin.read()\nprint(%r)\n" % json.dumps([GOOD]))
            out = os.path.join(tmp, "out")
            for _ in range(3):
                w.main(["--export", export, "--out", out, "--model-cmd", f"{sys.executable} {fake}",
                        "--skip-existing", "--limit", "1"])
            self.assertEqual(sorted(os.listdir(out)), ["c0.json", "c1.json", "c2.json"])

    def test_the_piece_cap_stops_between_conversations(self):
        convs = [dict(CONV, uuid=f"c{i}") for i in range(3)]
        with tempfile.TemporaryDirectory() as tmp:
            export = os.path.join(tmp, "e.json")
            _dump(convs, export)
            fake = os.path.join(tmp, "f.py")
            with open(fake, "w") as f:
                f.write("import sys\nsys.stdin.read()\nprint(%r)\n" % json.dumps([GOOD]))
            out = os.path.join(tmp, "out")
            w.main(["--export", export, "--out", out, "--model-cmd", f"{sys.executable} {fake}", "--max-pieces", "2"])
            self.assertEqual(len(os.listdir(out)), 2)

    def test_an_unknown_time_zone_is_refused(self):
        with self.assertRaises(SystemExit):
            w.main(["--export", "x.json", "--out", "y", "--timezone", "Mars/Olympus"])

    def test_each_piece_is_written_with_its_own_date_and_only_its_own_lines(self):
        conv = {"uuid": "long", "name": "A long chat", "created_at": "2023-07-14T10:00:00Z", "chat_messages": [
            {"uuid": "D1:1", "sender": "human", "created_at": "2023-07-14T10:00:00Z",
             "text": "Caroline: I signed up for the pottery class today."},
            {"uuid": "D2:1", "sender": "human", "created_at": "2023-07-20T10:00:00Z",
             "text": "Caroline: Yesterday I made my first bowl at pottery."}]}
        day1 = [{"topic": "Creative", "source_line_ids": ["D1:1"], "source_session_date": "14 July, 2023",
                 "excerpt": "I signed up for the pottery class today.", "summary": "Caroline signed up for pottery."}]
        day2 = [{"topic": "Creative", "source_line_ids": ["D2:1"], "source_session_date": "20 July, 2023",
                 "excerpt": "Yesterday I made my first bowl at pottery.",
                 "summary": "Caroline made her first bowl on 19 July 2023 (the day before this session)."},
                {"topic": "Creative", "source_line_ids": ["D1:1"], "source_session_date": "20 July, 2023",
                 "excerpt": "I signed up for the pottery class today.", "summary": "Cites a line from another day."}]
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "m.py")
            with open(path, "w") as f:   # answers by which day's calendar it was given
                f.write("import sys\np = sys.stdin.read()\nprint(%r if 'Session date: 2023-07-20' in p else %r)\n"
                        % (json.dumps(day2), json.dumps(day1)))
            res = w.write_conversation(conv, "T", f"{sys.executable} {path}", 30)
        self.assertEqual([p["date"] for p in res["pieces"]], ["2023-07-14", "2023-07-20"])
        self.assertEqual(sorted(m["source_session_date"] for m in res["memories"]), ["2023-07-14", "2023-07-20"])
        self.assertEqual(len(res["rejected"]), 1)                      # a piece may cite only its own lines
        self.assertIn("not in this session", res["rejected"][0]["reason"])
        self.assertNotIn("error", res)


class Dates(unittest.TestCase):
    """Benchmark 17 Oct: LoCoMo conv 0 s8 (Saturday 15 July 2023), "Last Friday I went to a council meeting" was
    written as "likely 7 July 2023"; the right day is 14 July. The writer reads dates off a calendar now, and a
    summary that can be PROVEN wrong is rejected."""
    LINE = "Caroline: Last Friday I went to a council meeting for adoption."

    def test_the_prompt_carries_the_weekday_and_a_labelled_calendar(self):
        p = w.build_prompt("T", "s8", "2023-07-15", [("D8:1", "user", self.LINE, None)])
        self.assertIn("Session weekday: Saturday", p)
        self.assertIn("Friday 14 July 2023 — yesterday, last Friday", p)
        self.assertIn("Saturday 15 July 2023 — THIS SESSION", p)
        self.assertIn("Friday 21 July 2023 — in 6 days, next Friday", p)

    def test_no_calendar_when_the_session_date_is_unknown(self):
        self.assertEqual(w.calendar_block(""), "")

    def test_the_benchmark_slip_is_rejected_both_ways(self):
        self.assertIn("hedged", w.check_dates("on the Friday before (likely 7 July 2023)", self.LINE, "2023-07-15"))
        self.assertIn("14 July", w.check_dates("She went on 7 July 2023.", self.LINE, "2023-07-15"))

    def test_the_right_day_passes(self):
        self.assertIsNone(w.check_dates("She went on Friday 14 July 2023 (the day before this session).",
                                        self.LINE, "2023-07-15"))

    def test_a_weekday_must_match_its_date(self):
        self.assertIn("Thursday", w.check_dates("They met on Thursday 14 July 2023.", "x", "2023-07-15"))

    def test_last_and_next_in_one_line_and_stated_dates_are_not_slips(self):
        line = "Last Friday I went, next Friday I go back, and on 7 July I went too."
        self.assertIsNone(w.check_dates("She went on 14 July 2023, returns 21 July 2023, and went on 7 July 2023.",
                                        line, "2023-07-15"))

    def test_a_slipped_memory_is_rejected_by_the_writer(self):
        lines = {"D8:1": self.LINE}
        t = {"topic": "Family", "source_line_ids": ["D8:1"], "source_session_date": "15 July, 2023",
             "excerpt": "Last Friday I went to a council meeting for adoption.",
             "summary": "Caroline went to an adoption council meeting on 7 July 2023."}
        clean, why = w.check_thread(t, lines, "2023-07-15")
        self.assertIsNone(clean)
        self.assertIn("14 July", why)
        clean, why = w.check_thread(dict(t, summary="Caroline went to an adoption council meeting on Friday "
                                                    "14 July 2023."), lines, "2023-07-15")
        self.assertIsNotNone(clean, why)

    def test_a_date_slip_gets_one_corrective_retry(self):
        conv = {"uuid": "s8", "name": "1:51 pm on 15 July, 2023", "created_at": "2023-07-15T13:51:00Z",
                "chat_messages": [{"uuid": "D8:1", "sender": "human", "text": self.LINE}]}
        mem = {"topic": "Family", "source_line_ids": ["D8:1"], "source_session_date": "15 July, 2023",
               "excerpt": "Last Friday I went to a council meeting for adoption."}
        wrong = json.dumps([dict(mem, summary="Caroline went to the meeting on 7 July 2023.")])
        right = json.dumps([dict(mem, summary="Caroline went to the meeting on Friday 14 July 2023.")])
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "m.py")
            with open(path, "w") as f:   # wrong first; right once it is told about the date error
                f.write("import sys\np = sys.stdin.read()\nprint(%r if 'date errors' in p else %r)\n" % (right, wrong))
            res = w.write_conversation(conv, "T", f"{sys.executable} {path}", 30)
            self.assertEqual(len(res["memories"]), 1)
            self.assertIn("14 July", res["memories"][0]["summary"])
            self.assertEqual(res["calls"][-1].get("retry"), "date")
            self.assertIn("last Friday = Friday 14 July 2023", w.calendar_block("2023-07-15"))

    def test_both_prompts_carry_the_calendar_rule(self):
        for name in [n for n in ("house", "public") if os.path.exists(w.PROMPTS[n])]:
            with open(w.PROMPTS[name], encoding="utf-8") as f:
                text = f.read()
            self.assertIn("READ it off the calendar", text)
            self.assertIn("Never hedge a date", text)


if __name__ == "__main__":
    unittest.main()
