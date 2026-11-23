# Scriptorium — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""On re-export the earlier context never pushes a model call past max_chars (oldest notes go first)."""
import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine"))
import write_notes as w


class ContextCap(unittest.TestCase):
    def _conv(self, notes, turns):
        return {"uuid": "c1", "name": "t", "created_at": "2026-11-23T10:00:00Z",
                "chat_messages": [{"uuid": "m1", "sender": "human", "text": "new question", "created_at": "2026-11-23T10:00:00Z"}],
                "context": {"notes": notes, "turns": turns, "left_out": 0}}

    def test_large_notes_are_trimmed_to_fit(self):
        prompts = []
        def fetch(prompt):
            prompts.append(prompt)
            return '{"memories": []}'
        notes = [f"note {i} " + "x" * 500 for i in range(20)]       # ~10,000 chars of earlier notes
        conv = self._conv(notes, [("human", "earlier turn")])
        orig = w._write_piece
        seen = {}
        def spy(title, date, lines, template, fetch_, retries, **kw):
            seen.update(kw)
            return [], [], [], None
        w._write_piece = spy
        try:
            w.write_conversation(conv, "T", None, 10, max_chars=5000)
        finally:
            w._write_piece = orig
        block = w.context_block(seen["context_lines"], seen["context_notes"], seen["context_left_out"])
        self.assertLessEqual(len(block), 2500)
        self.assertTrue(seen["context_notes"][0].startswith("("), "says how many notes were left out")
        self.assertIn("note 19", "\\n".join(seen["context_notes"]), "the newest note is kept")


if __name__ == "__main__":
    unittest.main()
