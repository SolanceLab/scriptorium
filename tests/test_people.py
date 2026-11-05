# Scriptorium — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""People cards: one card per recurring person, built only from memories already written and checked.
Run: python3 -m unittest discover -s engine/tests"""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
# The module keeps the writers in engine/writer; the public copy puts them in engine/ beside tests/.
for _d in (os.path.join(HERE, "..", "writer"), os.path.join(HERE, "..", "engine")):
    if os.path.isdir(_d):
        sys.path.insert(0, _d)
import write_people as wp  # noqa: E402

CARD = [{"person": "Nate", "aspect": "desserts", "memories": ["M1", "M2"],
         "summary": "Nate's favourite dessert is coconut ice cream (8 May 2023) and he baked a dairy-free chocolate "
                    "cake for his sister (12 June 2023)."}]
EXPORT = [
    {"uuid": "s1", "name": "s1", "created_at": "2023-05-08T10:00:00Z", "chat_messages": [
        {"uuid": "D1:1", "sender": "human", "text": "Nate: I love coconut ice cream, it's my favourite dessert."}]},
    {"uuid": "s2", "name": "s2", "created_at": "2023-06-12T10:00:00Z", "chat_messages": [
        {"uuid": "D2:1", "sender": "human", "text": "Nate: I baked a dairy-free chocolate cake for my sister."}]},
]
MEMS = {
    "s1.json": {"conversation_id": "s1", "session_date": "2023-05-08", "memories": [
        {"topic": "Leisure", "source_line_ids": ["D1:1"], "excerpt": "I love coconut ice cream, it's my favourite dessert.",
         "summary": "Nate said coconut ice cream is his favourite dessert."}]},
    "s2.json": {"conversation_id": "s2", "session_date": "2023-06-12", "memories": [
        {"topic": "Family", "source_line_ids": ["D2:1"], "excerpt": "I baked a dairy-free chocolate cake for my sister.",
         "summary": "Nate baked a dairy-free chocolate cake for his sister."}]},
}


class People(unittest.TestCase):
    def _setup(self, tmp):
        export = os.path.join(tmp, "conversations.json")
        with open(export, "w") as f:
            json.dump(EXPORT, f)
        mem = os.path.join(tmp, "mem")
        os.makedirs(mem)
        for name, doc in MEMS.items():
            with open(os.path.join(mem, name), "w") as f:
                json.dump(doc, f)
        return export, mem

    def _fake(self, tmp, answer):
        path = os.path.join(tmp, "fake.py")
        with open(path, "w") as f:
            f.write("import sys\nsys.stdin.read()\nprint(%r)\n" % answer)
        return f"{sys.executable} {path}"

    def setUp(self):
        self._old = wp.MIN_MEMORIES
        wp.MIN_MEMORIES = 2            # the small fixture has 2 memories; the threshold itself is tested below

    def tearDown(self):
        wp.MIN_MEMORIES = self._old

    def test_a_card_joins_facts_from_two_sessions_with_their_real_words_and_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            export, mem = self._setup(tmp)
            out = os.path.join(tmp, "out")
            rc = wp.main(["--export", export, "--memories", mem, "--out", out,
                          "--model-cmd", self._fake(tmp, json.dumps(CARD))])
            self.assertEqual(rc, 0)
            card = json.load(open(os.path.join(out, "person-nate.json")))
            self.assertEqual(card["conversation_id"], "person:nate")
            item = card["memories"][0]
            self.assertEqual(item["source_line_ids"], ["D1:1", "D2:1"])
            self.assertIn("coconut ice cream", item["excerpt"])
            self.assertIn("dairy-free chocolate cake", item["excerpt"])
            self.assertEqual(item["first_session_date"], "2023-05-08")
            self.assertEqual(item["source_session_date"], "2023-06-12")
            self.assertEqual(item["memories_used"], ["M1", "M2"])

    def test_the_prompt_shows_memories_by_number_not_their_quotes(self):
        with tempfile.TemporaryDirectory() as tmp:
            _export, mem = self._setup(tmp)
            p = wp.build_prompt("T", wp.load_memories(mem), [])
            self.assertIn("M1 [2023-05-08] Nate said coconut ice cream", p)
            self.assertNotIn("it's my favourite dessert.", p)    # the quote is assembled by code, never by the model

    def test_an_unknown_memory_number_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            export, mem = self._setup(tmp)
            res = wp.write_people(export, mem, os.path.join(tmp, "out"),
                                  self._fake(tmp, json.dumps([dict(CARD[0], memories=["M9"])])))
            self.assertEqual(res["people"], {})
            self.assertIn("do not exist", res["rejected"][0]["reason"])

    def test_an_item_without_memories_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            export, mem = self._setup(tmp)
            res = wp.write_people(export, mem, os.path.join(tmp, "out"),
                                  self._fake(tmp, json.dumps([dict(CARD[0], memories=[])])))
            self.assertEqual(res["people"], {})

    def test_a_hedged_date_in_a_card_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            export, mem = self._setup(tmp)
            bad = [dict(CARD[0], summary="Nate baked a cake for his sister (likely 12 June 2023).")]
            res = wp.write_people(export, mem, os.path.join(tmp, "out"), self._fake(tmp, json.dumps(bad)))
            self.assertIn("hedged", res["rejected"][0]["reason"])

    def test_an_ambiguous_line_id_never_backs_a_fact(self):
        dup = EXPORT + [{"uuid": "s3", "name": "s3", "created_at": "2023-07-01T10:00:00Z", "chat_messages": [
            {"uuid": "D1:1", "sender": "human", "text": "Someone else entirely."}]}]
        with tempfile.TemporaryDirectory() as tmp:
            _export, mem = self._setup(tmp)
            export = os.path.join(tmp, "dup.json")
            with open(export, "w") as f:
                json.dump(dup, f)
            res = wp.write_people(export, mem, os.path.join(tmp, "out"), self._fake(tmp, json.dumps(CARD)))
            self.assertEqual(res["people"], {})
            self.assertIn("not in this session", res["rejected"][0]["reason"])

    def test_existing_cards_are_given_back_to_the_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            export, mem = self._setup(tmp)
            out = os.path.join(tmp, "out")
            wp.main(["--export", export, "--memories", mem, "--out", out,
                     "--model-cmd", self._fake(tmp, json.dumps(CARD))])
            cards = wp.load_cards(out)
            self.assertEqual(cards[0]["person"], "Nate")
            self.assertIn("Existing cards", wp.build_prompt("T", wp.load_memories(mem), cards))

    def test_a_card_may_repeat_dates_never_make_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            export, mem = self._setup(tmp)
            bad = [dict(CARD[0], summary="Nate liked coconut ice cream (1 January 1999) and baked a cake (2 February 2000).")]
            res = wp.write_people(export, mem, os.path.join(tmp, "out"), self._fake(tmp, json.dumps(bad)))
            self.assertEqual(res["people"], {})
            self.assertIn("is not in the memories", res["rejected"][0]["reason"])
            ok = wp.write_people(export, mem, os.path.join(tmp, "out"), self._fake(tmp, json.dumps(CARD)))
            self.assertIn("nate", ok["people"])          # 8 May and 12 June 2023 are the memories' own days

    def test_an_explicit_year_one_year_off_is_refused_a_yearless_date_is_not(self):
        used = [{"date": "2023-05-08", "summary": "Nate said coconut ice cream is his favourite.", "excerpt": ""}]
        self.assertIn("is not in the memories", wp.dates_backed("Nate said so on 8 May 2024.", used))
        self.assertIsNone(wp.dates_backed("Nate said so on 8 May 2023.", used))
        self.assertIsNone(wp.dates_backed("Nate said so on 8 May.", used))

    def test_iso_dates_are_checked_too(self):
        used = [{"date": "2023-05-08", "summary": "Nate said so.", "excerpt": ""}]
        self.assertIn("is not in the memories", wp.dates_backed("Nate said so on 2024-05-08.", used))
        self.assertIsNone(wp.dates_backed("Nate said so on 2023-05-08.", used))

    def test_numeric_dates_are_checked_in_every_reading(self):
        used = [{"date": "2023-05-08", "summary": "Nate said so.", "excerpt": ""}]
        for bad in ("05/08/2024", "2024/05/08", "8-5-2024"):
            self.assertIn("is not in the memories", wp.dates_backed(f"Nate said so on {bad}.", used), bad)
        for ok in ("08/05/2023", "5/8/2023"):
            self.assertIsNone(wp.dates_backed(f"Nate said so on {ok}.", used), ok)

    def _one_item(self, aspect, summary):
        with tempfile.TemporaryDirectory() as tmp:
            export, mem = self._setup(tmp)
            item = [dict(CARD[0], aspect=aspect, summary=summary)]
            return wp.write_people(export, mem, os.path.join(tmp, "out"), self._fake(tmp, json.dumps(item)))

    def test_a_fact_item_must_carry_a_date_but_a_who_they_are_item_need_not(self):
        res = self._one_item("desserts", "Nate likes coconut ice cream.")
        self.assertEqual(res["people"], {})
        self.assertEqual(res["rejected"][0]["reason"], "a fact item must carry the date its memories give")
        res = self._one_item("who they are", "Nate is a home baker.")
        self.assertEqual(res["rejected"], [])

    def test_underscore_hyphen_and_space_are_one_person(self):
        self.assertEqual(wp.person_key("Jean_Luc"), wp.person_key("Jean-Luc"))
        self.assertEqual(wp.person_key("Jean-Luc"), wp.person_key("Jean Luc"))

    def test_a_run_that_answers_with_no_cards_is_a_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            export, mem = self._setup(tmp)
            rc = wp.main(["--export", export, "--memories", mem, "--out", os.path.join(tmp, "out"),
                          "--model-cmd", self._fake(tmp, "[]")])
            self.assertEqual(rc, 0)
            rc = wp.main(["--export", export, "--memories", mem, "--out", os.path.join(tmp, "out"),
                          "--model-cmd", self._fake(tmp, "not json")])
            self.assertEqual(rc, 1)

    def test_two_long_names_never_share_a_card(self):
        a, b = "x" * 90 + " one", "x" * 90 + " two"
        self.assertNotEqual(wp.person_key(a), wp.person_key(b))

    def test_a_run_with_no_cards_still_sets_old_ones_aside_but_a_failed_run_does_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            export, mem = self._setup(tmp)
            out = os.path.join(tmp, "out")
            os.makedirs(out)
            ghost = os.path.join(out, "person-ghost.json")
            with open(ghost, "w") as f:
                json.dump({"conversation_id": "person:ghost", "memories": []}, f)
            wp.main(["--export", export, "--memories", mem, "--out", out, "--model-cmd", self._fake(tmp, "not json")])
            self.assertTrue(os.path.exists(ghost))                   # failed run: nothing touched
            wp.main(["--export", export, "--memories", mem, "--out", out, "--model-cmd", self._fake(tmp, "[]")])
            self.assertEqual(os.listdir(out), ["person-ghost.json.stale"])

    def test_setting_aside_twice_keeps_both_copies(self):
        with tempfile.TemporaryDirectory() as tmp:
            export, mem = self._setup(tmp)
            out = os.path.join(tmp, "out")
            os.makedirs(out)
            for _ in range(2):
                with open(os.path.join(out, "person-ghost.json"), "w") as f:
                    json.dump({"conversation_id": "person:ghost", "memories": []}, f)
                wp.main(["--export", export, "--memories", mem, "--out", out,
                         "--model-cmd", self._fake(tmp, json.dumps(CARD))])
            self.assertIn("person-ghost.json.stale", os.listdir(out))
            self.assertIn("person-ghost.json.stale.2", os.listdir(out))

    def test_a_numeric_date_written_in_a_memory_is_backed(self):
        used = [{"date": "2023-05-08", "summary": "Nate was born on 05/08/1990.", "excerpt": ""}]
        self.assertIsNone(wp.dates_backed("Nate was born on 05/08/1990.", used))

    def test_only_the_identity_item_may_carry_no_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            export, mem = self._setup(tmp)
            item = dict(CARD[0], aspect="people who helped", summary="Nate was helped by his sister.")
            res = wp.write_people(export, mem, os.path.join(tmp, "out"), self._fake(tmp, json.dumps([item])))
            self.assertIn("must carry the date", res["rejected"][0]["reason"])

    def test_names_in_every_script_keep_their_own_cards(self):
        keys = {wp.person_key(n) for n in ("李", "محمد", "李 明", "Nate", "Zoë")}
        self.assertEqual(len(keys), 5)
        self.assertEqual(wp.person_key("Nate"), "nate")
        self.assertTrue(wp.person_key("!!!").startswith("person-"))

    def test_a_card_this_run_did_not_write_is_set_aside_not_deleted(self):
        with tempfile.TemporaryDirectory() as tmp:
            export, mem = self._setup(tmp)
            out = os.path.join(tmp, "out")
            os.makedirs(out)
            with open(os.path.join(out, "person-ghost.json"), "w") as f:
                json.dump({"conversation_id": "person:ghost", "memories": []}, f)
            wp.main(["--export", export, "--memories", mem, "--out", out,
                     "--model-cmd", self._fake(tmp, json.dumps(CARD))])
            self.assertEqual(sorted(os.listdir(out)), ["person-ghost.json.stale", "person-nate.json"])

    def test_each_memory_keeps_its_own_day_and_conversation(self):
        with tempfile.TemporaryDirectory() as tmp:
            mem = os.path.join(tmp, "mem")
            os.makedirs(mem)
            with open(os.path.join(mem, "long.json"), "w") as f:     # one long chat, two days
                json.dump({"conversation_id": "long", "session_date": "2023-07-14", "memories": [
                    {"source_line_ids": ["a"], "summary": "day one", "source_session_date": "2023-07-14"},
                    {"source_line_ids": ["b"], "summary": "day two", "source_session_date": "2023-07-20"}]}, f)
            got = {r["summary"]: (r["date"], r["cid"]) for r in wp.load_memories(mem)}
            self.assertEqual(got["day two"], ("2023-07-20", "long"))

    def test_two_conversations_on_one_day_are_two_sessions(self):
        same_day = {k: dict(v, session_date="2023-05-08") for k, v in MEMS.items()}
        same_day["s2.json"] = dict(same_day["s2.json"], memories=[dict(m, source_session_date="2023-05-08")
                                                                    for m in MEMS["s2.json"]["memories"]])
        with tempfile.TemporaryDirectory() as tmp:
            export, mem = self._setup(tmp)
            for name, doc in same_day.items():
                with open(os.path.join(mem, name), "w") as f:
                    json.dump(doc, f)
            same_day_card = [dict(CARD[0], summary="Nate likes coconut ice cream and baked a dairy-free chocolate "
                                                    "cake for his sister (both 8 May 2023).")]
            res = wp.write_people(export, mem, os.path.join(tmp, "out"), self._fake(tmp, json.dumps(same_day_card)))
            self.assertIn("nate", res["people"])                     # 2 memories (MIN 2 here), 2 sessions

    def test_who_gets_a_card_is_decided_by_code_not_the_model(self):
        one_off = {"person": "Coach Rachel", "aspect": "who they are", "memories": ["M1"],
                   "summary": "Coach Rachel was mentioned once."}
        with tempfile.TemporaryDirectory() as tmp:
            export, mem = self._setup(tmp)
            # Nate rests on 2 memories from 2 sessions: under MIN_MEMORIES (3) as well, so with the real threshold
            # both are dropped; lower it to show the rule is applied per person, deterministically.
            old = wp.MIN_MEMORIES
            try:
                wp.MIN_MEMORIES = 2
                res = wp.write_people(export, mem, os.path.join(tmp, "out"),
                                      self._fake(tmp, json.dumps(CARD + [one_off])))
            finally:
                wp.MIN_MEMORIES = old
            self.assertIn("nate", res["people"])
            self.assertNotIn("coach-rachel", res["people"])
            self.assertEqual(res["dropped"]["Coach Rachel"], {"memories": 1, "sessions": 1})

    def test_the_people_prompt_is_public(self):
        text = open(wp.PROMPT_FILE, encoding="utf-8").read()
        for word in ("Solance", "House", "All rights " + "reserved"):
            self.assertNotIn(word, text)


if __name__ == "__main__":
    unittest.main()
