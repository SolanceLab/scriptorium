# Scriptorium — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine"))
import write_notes as w


class ContextBudget(unittest.TestCase):
    def test_context_comes_off_each_calls_budget(self):
        lines = [(f"m{i}", "user", "x" * 90, "2026-01-01T10:00:00Z") for i in range(20)]
        seen = []
        orig_split, orig_lines = w.split_pieces, w.claude_lines
        w.split_pieces = lambda ls, d, mx, tz=None: (seen.append(mx), orig_split(ls, d, mx, tz))[1]
        w.claude_lines = lambda c: ("c", "t", lines)
        try:
            conv = {"context": {"notes": ["n" * 1000], "turns": [], "left_out": 0}}
            w.write_conversation(conv, "T", "x", 1, max_chars=5000, api_call=lambda p, t: ("[]", {}))
        finally:
            w.split_pieces, w.claude_lines = orig_split, orig_lines
        self.assertLess(seen[0], 5000)
        self.assertGreaterEqual(seen[0], 1250)


if __name__ == "__main__":
    unittest.main()
