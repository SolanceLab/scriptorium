# Scriptorium — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""The writer keeps only memories its lines prove. Run: python3 -m unittest discover -s engine/tests"""
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
# The module keeps the writers in engine/writer; the public copy puts them in engine/ beside tests/.
for _d in (os.path.join(HERE, "..", "writer"), os.path.join(HERE, "..", "engine")):
    if os.path.isdir(_d):
        sys.path.insert(0, _d)
import write_memories as w  # noqa: E402

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
            w.main(["--export", self._export(tmp), "--out", out, "--model-cmd", cmd, "--prompt", "public"])
            rec = _load(os.path.join(out, "conv-1.json"))["writer"]
            self.assertEqual(rec["model_cmd"], cmd)
            self.assertEqual(rec["prompt_file"], "prompt-public.md")
            self.assertEqual(rec["prompt_sha256"], w.sha256_file(w.PROMPTS["public"]))
            self.assertIn("writer_commit", rec)
            self.assertIn("claude_version", rec)

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
            self.assertEqual(open(log).read(), "xx")                  # conv-2 and conv-3 redone, conv-1 left alone
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
        text = open(w.PROMPTS["public"], encoding="utf-8").read()
        for word in ("Solance", "House", "keynote", "Keynote", "All rights " + "reserved"):
            self.assertNotIn(word, text)
        house = open(w.PROMPTS["house"], encoding="utf-8").read() if os.path.exists(w.PROMPTS["house"]) else None
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
            text = open(w.PROMPTS[name], encoding="utf-8").read()
            self.assertIn("READ it off the calendar", text)
            self.assertIn("Never hedge a date", text)


if __name__ == "__main__":
    unittest.main()
