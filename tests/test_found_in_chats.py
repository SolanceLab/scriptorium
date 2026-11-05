# Scriptorium — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
# Scriptorium — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""Found in chats: every model answer is checked in code before it is kept.
Run: python3 -m unittest test_found_in_chats -v"""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
for _d in (os.path.join(HERE, "..", "writer"), os.path.join(HERE, "..", "engine")):
    if os.path.isdir(_d):
        sys.path.insert(0, _d)
import found_in_chats as fc  # noqa: E402


def msg(i, text, sender="Ona", said_at="2026-10-20T10:00:00+07:00", room="general", thread=""):
    return {"id": str(i), "sender": sender, "room": room, "thread": thread, "said_at": said_at, "text": text}


def card(name="Nate", **kw):
    c = {"id": "p-" + name, "name": name, "kind": "human", "aka": []}
    c.update(kw)
    return c


def fake(tmp, body):
    path = os.path.join(tmp, "fake.py")
    with open(path, "w") as f:
        f.write(body)
    return "%s %s" % (sys.executable, path)


def answer(tmp, items):
    return fake(tmp, "import sys\nsys.stdin.read()\nprint(%r)\n" % json.dumps({"facts": items}))


OWNERS = ["Ona", "Pim"]


def run(cards, msgs, items, **kw):
    kw.setdefault("owners", OWNERS)
    with tempfile.TemporaryDirectory() as tmp:
        return fc.find(cards, msgs, answer(tmp, items), **kw)


class Matching(unittest.TestCase):
    def names(self, c, text, sender="Ona"):
        return fc.group_messages([c], [msg(1, text, sender)], OWNERS)

    def test_whole_word_only(self):
        self.assertEqual(self.names(card("Ami"), "I saw Amira today"), {})
        self.assertEqual(self.names(card("Amira"), "I saw Ami today"), {})
        self.assertEqual(len(self.names(card("Amira"), "I saw amira today")), 1)

    def test_short_name_needs_capital(self):
        self.assertEqual(self.names(card("Ami"), "the ami is here"), {})
        self.assertEqual(len(self.names(card("Ami"), "Ami is here")), 1)

    def test_accent_kept(self):
        self.assertEqual(self.names(card("Elías"), "Elias came"), {})
        self.assertEqual(len(self.names(card("Elías"), "Elías came")), 1)

    def test_aka_and_sender(self):
        self.assertEqual(len(self.names(card("Nathaniel", aka=["Nate"]), "Nate came")), 1)
        self.assertEqual(len(self.names(card("Nathaniel"), "hello", sender="nathaniel \u2728")), 1)
        self.assertEqual(self.names(card("Nathaniel"), "hello", sender="Someone"), {})

    def test_cap_keeps_newest(self):
        msgs = [msg(i, "Nate here", said_at="2026-%02d-%02dT10:00:00+00:00" % (1 + i // 28, 1 + i % 28))
                for i in range(70)]
        got = fc.group_messages([card("Nate")], msgs)[0]
        self.assertEqual(len(got), 60)
        self.assertEqual(got[-1]["id"], "69")
        self.assertEqual(got[0]["id"], "10")


class Checks(unittest.TestCase):
    M = [msg(1, "Nate works as a nurse and was born on 14 March."), msg(2, "Mia is not a nurse anymore"),
         msg(3, "Nate is not coming", said_at="2026-10-20T20:30:00+00:00")]

    def test_plain_fact_and_field_split(self):
        r = run([card()], self.M, [
            {"fact": "Works as a nurse.", "quote": "works as a nurse", "message_id": "1"},
            {"fact": "Born 14 March.", "quote": "born on 14 March", "message_id": "1", "field": "birthday", "value": "03-14"}])
        self.assertEqual([f["fact"] for f in r["facts"]], ["Works as a nurse."])
        self.assertEqual(r["facts"][0]["said_on"], "2026-10-20")
        self.assertEqual(r["facts"][0]["message_ids"], ["1"])
        self.assertEqual(r["facts"][0]["source"], "general")
        self.assertEqual(r["field_suggestions"][0]["field"], "birthday")
        self.assertEqual(r["field_suggestions"][0]["value"], "03-14")
        self.assertEqual(r["rejected"], [])

    def test_quote_not_in_message_rejected(self):
        r = run([card()], self.M, [{"fact": "x", "quote": "is a surgeon", "message_id": "1"}])
        self.assertEqual(r["facts"], [])
        self.assertIn("word for word", r["rejected"][0]["why"])

    def test_message_from_another_persons_list_rejected(self):
        r = run([card()], self.M, [{"fact": "x", "quote": "is not a nurse", "message_id": "2"}])
        self.assertIn("not one of this person's", r["rejected"][0]["why"])

    def test_negative_quote_rejected(self):
        r = run([card()], self.M, [{"fact": "Is not coming.", "quote": "is not coming", "message_id": "3"}])
        self.assertIn("negative", r["rejected"][0]["why"])

    def test_negative_allowed_for_care(self):
        m = [msg(1, "Nate is not to be told about the dog")]
        r = run([card()], m, [{"fact": "Is not to be told about the dog.", "quote": "is not to be told about the dog", "message_id": "1",
                               "field": "care", "value": "not to be told about the dog"}])
        self.assertEqual(len(r["field_suggestions"]), 1, r["rejected"])

    def test_day_of_is_the_timestamps_own_date_unless_a_zone_is_set(self):
        self.assertEqual(fc.day_of("2026-10-20T20:30:00+00:00"), "2026-10-20")
        self.assertEqual(fc.day_of("2026-10-20T20:30:00Z"), "2026-10-20")
        fc.DAY_TZ = "Asia/Jakarta"
        try:
            self.assertEqual(fc.day_of("2026-10-20T20:30:00Z"), "2026-10-21")
        finally:
            fc.DAY_TZ = None

    def test_duplicate_collapses(self):
        it = {"fact": "Works as a nurse.", "quote": "works as a nurse", "message_id": "1"}
        r = run([card()], self.M, [it, dict(it, fact="works  as a NURSE.")])
        self.assertEqual(len(r["facts"]), 1)

    def test_bad_field_value_rejected(self):
        r = run([card()], self.M, [{"fact": "Born on 14 March.", "quote": "born on 14 March", "message_id": "1",
                                    "field": "birthday", "value": "05-14"}])
        self.assertEqual(r["field_suggestions"], [])
        self.assertIn("month", r["rejected"][0]["why"])


class SentenceChecks(unittest.TestCase):
    def one(self, text, fact, quote, **kw):
        return run([card()], [msg(1, text)], [dict({"fact": fact, "quote": quote, "message_id": "1"}, **kw)])

    def test_fact_must_match_quote(self):
        r = self.one("Nate works as a nurse.", "Nate is a surgeon.", "works as a nurse")
        self.assertEqual(r["facts"], [])
        self.assertIn("nothing to do", r["rejected"][0]["why"])

    def test_fact_sharing_a_word_with_message_kept(self):
        r = self.one("Nate lives in Lisbon.", "Lives in Porto.", "lives in Lisbon")
        self.assertEqual(len(r["facts"]), 1, r["rejected"])

    def test_fact_with_nothing_in_message_rejected(self):
        r = run([card()], [msg(1, "Nate went swimming.")],
                [{"fact": "Owns a bakery.", "quote": "went swimming", "message_id": "1"}])
        self.assertEqual(r["facts"], [])
        self.assertIn("nothing to do", r["rejected"][0]["why"])

    def test_fact_word_forms_match(self):
        r = run([card()], [msg(1, "Nate and I dove at the reef and a bull shark swam past.")],
                [{"fact": "Met a bull shark while diving.", "quote": "a bull shark swam past", "message_id": "1"}])
        self.assertEqual(len(r["facts"]), 1, r["rejected"])
        r = run([card()], [msg(1, "Nate is building a payroll app for her office.")],
                [{"fact": "Works on a payroll app.", "quote": "building a payroll app", "message_id": "1"}])
        self.assertEqual(len(r["facts"]), 1, r["rejected"])

    def test_fact_paraphrase_of_quote_passes(self):
        r = self.one("Nate works as a nurse.", "Works as a nurse.", "works as a nurse")
        self.assertEqual(len(r["facts"]), 1, r["rejected"])

    def test_negation_outside_quote_rejected(self):
        r = self.one("Nate does not work as a nurse.", "Works as a nurse.", "work as a nurse")
        self.assertEqual(r["facts"], [])
        self.assertIn("negative", r["rejected"][0]["why"])

    def test_negation_in_other_sentence_does_not_block(self):
        r = self.one("It is not raining. Nate works as a nurse.", "Works as a nurse.", "works as a nurse")
        self.assertEqual(len(r["facts"]), 1, r["rejected"])

    def test_name_in_message_not_sentence_is_enough(self):
        r = run([card("Rina")], [msg(1, "Rina told me today: her boss gave her a team account")],
                [{"fact": "Her boss gave her a team account.", "quote": "her boss gave her a team account",
                  "message_id": "1"}])
        self.assertEqual(len(r["facts"]), 1, r["rejected"])

    def test_name_must_be_in_message(self):
        m = msg(1, "Mia works as a nurse.", sender="Zed")
        kind, entry, why = fc.check_fact(card("Nate"), {"1": m},
                                         {"fact": "Works as a nurse.", "quote": "works as a nurse", "message_id": "1"})
        self.assertIsNone(entry)
        self.assertIn("not named in the message", why)

    def test_sender_still_counts(self):
        r = run([card()], [msg(1, "I work as a nurse.", sender="Nate")],
                [{"fact": "Works as a nurse.", "quote": "work as a nurse", "message_id": "1"}])
        self.assertEqual(len(r["facts"]), 1, r["rejected"])

    def test_abbreviation_does_not_split(self):
        self.assertEqual(len(fc.sentences_of("Nate was born on Jan. 3 in Rome.")), 1)


class Review(unittest.TestCase):
    def test_same_name_people_do_not_share_messages(self):
        a, b = card("Kai", id="k1"), card("Kai", id="k2")
        self.assertEqual(fc.group_messages([a, b], [msg(1, "Kai has a dog.")]), {})
        g = fc.group_messages([a, b], [msg(1, "Kai has a dog.", sender="Kai")])
        self.assertEqual(g, {})          # the sender fits both cards, so neither gets the message
        a2 = card("Kai", id="k1", aka=["Kaito"])
        g = fc.group_messages([a2, b], [msg(1, "Kaito has a dog.")])
        self.assertEqual(list(g), [0])

    def test_ambiguous_sender_goes_to_none(self):
        a = card("Kai Rivers", id="k1", aka=["Kai"])
        b = card("Kai Moreno", id="k2", aka=["Kai"])
        self.assertEqual(fc.group_messages([a, b], [msg(1, "hello", sender="Kai")]), {})
        g = fc.group_messages([a, b], [msg(1, "hello", sender="Kai Rivers")])
        self.assertEqual(list(g), [0])

    def test_owner_cards_skipped(self):
        self.assertEqual(fc.group_messages([card("Ona"), card("Pim")], [msg(1, "Ona and Pim", sender="Ona")], OWNERS), {})

    def test_owner_card_found_by_nickname(self):
        # card "Ona Vale" with aka "Ona" is the owner's card under --owner Ona
        self.assertTrue(fc.is_owner_card(card("Ona Vale", aka=["Ona"]), OWNERS))
        self.assertEqual(fc.group_messages([card("Ona Vale", aka=["Ona"]), card("Pim")],
                                           [msg(1, "Ona Vale went home", sender="Pim")], OWNERS), {})

    def test_fact_about_owner_rejected(self):
        r = run([card("Nate")], [msg(1, "Nate says Ona works as a nurse.")],
                [{"fact": "Ona works as a nurse.", "quote": "Nate says Ona works as a nurse.", "message_id": "1"}])
        self.assertEqual(r["facts"], [])
        self.assertEqual(r["rejected"][0]["why"], "fact about the owner")
        r = run([card("Nate")], [msg(1, "I work as a nurse with Nate.", sender="Ona")],
                [{"fact": "Works as a nurse.", "quote": "I work as a nurse with Nate.", "message_id": "1"}])
        self.assertEqual(r["facts"], [])
        self.assertEqual(r["rejected"][0]["why"], "fact about the owner")

    def test_conflicting_field_values_both_rejected(self):
        ms = [msg(1, "Nate was born on 14 March."), msg(2, "Nate was born on 15 March.")]
        r = run([card("Nate")], ms, [
            {"fact": "Born 14 March.", "quote": "born on 14 March", "message_id": "1", "field": "birthday", "value": "03-14"},
            {"fact": "Born 15 March.", "quote": "born on 15 March", "message_id": "2", "field": "birthday", "value": "03-15"}])
        self.assertEqual(r["field_suggestions"], [])
        self.assertEqual([x["why"] for x in r["rejected"]], ["conflicting values for birthday; check by hand"] * 2)

    def test_opposite_meaning_rejected(self):
        r = run([card("Nate")], [msg(1, "Nate hated dogs.")],
                [{"fact": "Likes dogs.", "quote": "Nate hated dogs.", "message_id": "1"}])
        self.assertEqual(r["facts"], [])
        self.assertEqual(len(r["rejected"]), 1)

    def test_past_tense_not_current(self):
        r = run([card("Nate")], [msg(1, "Nate worked as a nurse.")],
                [{"fact": "Works as a nurse.", "quote": "Nate worked as a nurse.", "message_id": "1"}])
        self.assertEqual(r["facts"], [])
        self.assertEqual(len(r["rejected"]), 1)

    def test_used_to_rejected(self):
        r = run([card("Nate")], [msg(1, "Nate used to live in Bali.")],
                [{"fact": "Nate lives in Bali.", "quote": "Nate used to live in Bali.", "message_id": "1"}])
        self.assertEqual(r["facts"], [])

    def test_irregular_past_rejected(self):
        for q, f in (("Nate was a nurse.", "Is a nurse."), ("Nate had two dogs.", "Has two dogs.")):
            r = run([card("Nate")], [msg(1, q)], [{"fact": f, "quote": q, "message_id": "1"}])
            self.assertEqual(r["facts"], [], q)
            self.assertEqual(len(r["rejected"]), 1, q)

    def test_house_name_later_in_fact_kept(self):
        q = "Nate captains the ship Ona will sail on in October."
        r = run([card("Nate")], [msg(1, q)],
                [{"fact": "Captains the ship Ona will sail on in October.", "quote": q, "message_id": "1"}])
        self.assertEqual(len(r["facts"]), 1, r["rejected"])

    def test_sentence_about_another_card_rejected(self):
        text = "Nate arrived. Mia opened a bakery."
        r = run([card("Nate"), card("Mia")], [msg(1, text)],
                [{"fact": "Opened a bakery.", "quote": "Mia opened a bakery.", "message_id": "1"}])
        nate = [x for x in r["rejected"] if x["name"] == "Nate"]
        self.assertEqual(nate[0]["why"], "the quoted sentence is about someone else")
        self.assertFalse([f for f in r["facts"] if f["name"] == "Nate"])

    def test_no_other_card_named_kept(self):
        q = "Rina told me today: her boss gave her a team account"
        r = run([card("Rina"), card("Mia")], [msg(1, q)],
                [{"fact": "Boss gave her a team account.", "quote": q, "message_id": "1"}])
        self.assertEqual(len(r["facts"]), 1, r["rejected"])

    def test_sender_is_the_person_kept(self):
        q = "Mia and I went diving"
        r = run([card("Rina"), card("Mia")], [msg(1, q, sender="Rina")],
                [{"fact": "Went diving.", "quote": q, "message_id": "1"}], only=["Rina"])
        self.assertEqual(len(r["facts"]), 1, r["rejected"])

    def test_fact_with_no_content_word_rejected(self):
        r = run([card("Nate")], [msg(1, "Nate has a son.")],
                [{"fact": "Has a son.", "quote": "Nate has a son.", "message_id": "1"}])
        self.assertEqual(r["facts"], [])
        self.assertIn("nothing to do", r["rejected"][0]["why"])

    def test_house_name_in_first_three_words_rejected(self):
        q = "Nate's wife Ona works as a nurse."
        r = run([card("Nate")], [msg(1, q)],
                [{"fact": "His wife Ona works as a nurse.", "quote": q, "message_id": "1"}])
        self.assertEqual(r["facts"], [])
        self.assertEqual(r["rejected"][0]["why"], "fact about the owner")

    def test_multiword_owner_name_starting_in_first_three_words_rejected(self):
        q = "His wife Mia Voss works as a nurse."
        r = run([card("Nate")], [msg(1, q, sender="Nate")],
                [{"fact": "His wife Mia Voss works as a nurse.", "quote": q, "message_id": "1"}],
                owners=["Mia Voss"])
        self.assertEqual(r["facts"], [])
        self.assertEqual(r["rejected"][0]["why"], "fact about the owner")

    def test_multiword_owner_name_later_in_fact_kept(self):
        q = "Nate works as a nurse at the clinic where Mia Voss volunteers."
        r = run([card("Nate")], [msg(1, q, sender="Nate")],
                [{"fact": "Works as a nurse at the clinic where Mia Voss volunteers.", "quote": q, "message_id": "1"}],
                owners=["Mia Voss"])
        self.assertEqual(len(r["facts"]), 1)

    def test_already_on_the_card_rejected(self):
        c = card("Rina", links=[{"name": "Jo", "relation": "partner"}])
        q = "Rina and Jo are partners, Rina keeps bees."
        r = run([c], [msg(1, q)], [{"fact": "Jo is Rina's partner.", "quote": "Rina and Jo are partners", "message_id": "1"},
                                   {"fact": "Keeps bees.", "quote": "Rina keeps bees", "message_id": "1"}])
        self.assertEqual([f["fact"] for f in r["facts"]], ["Keeps bees."])
        self.assertEqual(r["rejected_by_reason"], {"already shown on the card": 1})

    def test_restated_link_needs_the_matching_relation(self):
        c = card("Rina", links=[{"name": "Jo", "relation": "partner"}])
        q = "Rina and Jo are close, Jo is her brother."
        r = run([c], [msg(1, q)], [{"fact": "Jo is Rina's brother.", "quote": "Jo is her brother", "message_id": "1"}])
        self.assertEqual(len(r["facts"]), 1)

    def test_relation_of_another_link_does_not_reject(self):
        c = card("Rina", links=[{"name": "Jo", "relation": "partner"}, {"name": "Kit", "relation": "brother"}])
        q = "Rina and Jo are close, Jo is her cousin's partner."
        r = run([c], [msg(1, q)], [{"fact": "Jo is Rina's brother.", "quote": "Rina and Jo are close", "message_id": "1"}])
        self.assertEqual(r["rejected_by_reason"].get("already shown on the card"), None)

    def test_house_name_in_other_sentence_of_message_rejected(self):
        t = "Nate arrived. The ship captain is Ona."
        r = run([card("Nate")], [msg(1, t)],
                [{"fact": "The ship captain is Ona.", "quote": "The ship captain is Ona.", "message_id": "1"}])
        self.assertEqual(r["facts"], [])

    def test_prompt_renders_links_and_leaves_no_placeholder(self):
        c = card("Rina", links=[{"name": "Jo", "relation": "partner"}])
        with open(fc.PROMPT_FILE, encoding="utf-8") as f:
            template = f.read()
        out = fc.build_prompt(template, c, [msg(1, "Rina keeps bees")])
        self.assertIn("Jo: partner", out)
        self.assertNotIn("{{LINKS}}", out)

    def test_rejected_counts_by_reason(self):
        r = run([card()], self.M if hasattr(self, "M") else [msg(1, "Nate is here")],
                [{"fact": "x", "quote": "nope", "message_id": "1"}])
        self.assertEqual(sum(r["rejected_by_reason"].values()), len(r["rejected"]))

    def test_similar_sender_name_is_not_house(self):
        for sender in ("Onette", "Pimwick"):
            q = "I work as a nurse and Nate agrees."
            r = run([card("Nate")], [msg(1, q, sender)],
                    [{"fact": "Nate works as a nurse.", "quote": q, "message_id": "1"}])
            self.assertEqual(len(r["facts"]), 1, sender)

    def test_quote_must_be_literal_not_whitespace_normalised(self):
        r = run([card("Nate")], [msg(1, "Nate works  as a nurse.")],
                [{"fact": "Works as a nurse.", "quote": "Nate works as a nurse.", "message_id": "1"}])
        self.assertEqual(r["facts"], [])
        self.assertEqual(len(r["rejected"]), 1)

    def test_present_fact_still_passes(self):
        r = run([card("Nate")], [msg(1, "Nate works as a nurse.")],
                [{"fact": "Works as a nurse.", "quote": "Nate works as a nurse.", "message_id": "1"}])
        self.assertEqual(len(r["facts"]), 1)

    def test_dedup_keeps_different_facts_from_one_quote(self):
        q = "Nate has two dogs and lives in Bali."
        r = run([card("Nate")], [msg(1, q)],
                [{"fact": "Has two dogs.", "quote": q, "message_id": "1"},
                 {"fact": "Lives in Bali.", "quote": q, "message_id": "1"},
                 {"fact": "Has two dogs.", "quote": q, "message_id": "1"}])
        self.assertEqual(len(r["facts"]), 2)


class Run(unittest.TestCase):
    def test_failed_call_goes_to_errors_and_run_continues(self):
        with tempfile.TemporaryDirectory() as tmp:
            body = ("import sys, json\np = sys.stdin.read()\n"
                    "if 'PERSON: Bob' in p:\n    sys.exit(3)\n"
                    "print(json.dumps({'facts': [{'fact': 'Is a cook.', 'quote': 'is a cook', 'message_id': '2'}]}))\n")
            r = fc.find([card("Bob"), card("Cy")], [msg(1, "Bob is a cook"), msg(2, "Cy is a cook")],
                        fake(tmp, body))
        self.assertEqual(len(r["errors"]), 1)
        self.assertEqual(r["errors"][0]["name"], "Bob")
        self.assertEqual([f["name"] for f in r["facts"]], ["Cy"])

    def test_cli_writes_output_and_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name, obj in (("m.json", [msg(1, "Bob is a cook"), msg(2, "Cy is a cook")]),
                              ("c.json", [card("Bob"), card("Cy")])):
                with open(os.path.join(tmp, name), "w") as f:
                    json.dump(obj, f)
            out = os.path.join(tmp, "o.json")
            rc = fc.main(["--messages", os.path.join(tmp, "m.json"), "--cards", os.path.join(tmp, "c.json"),
                          "--out", out, "--model-cmd", answer(tmp, []), "--only", "cy"])
            self.assertEqual(rc, 0)
            with open(out) as f:
                doc = json.load(f)
            self.assertEqual(doc["people_seen"], 1)
            self.assertEqual(set(doc), {"generated_at", "model", "people_seen", "facts", "field_suggestions",
                                        "rejected", "rejected_by_reason", "errors"})


if __name__ == "__main__":
    unittest.main()
