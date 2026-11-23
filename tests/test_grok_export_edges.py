# Scriptorium — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""Grok export edge cases: unhashable leaf, naive timestamps. Run: python3 -m unittest test_grok_export_edges -v"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "engine"))
import grok_export as grok  # noqa: E402
from test_grok_export import chat  # noqa: E402


class Edges(unittest.TestCase):
    def test_unhashable_leaf_falls_back_to_latest(self):
        for bad in ({"a": 1}, ["r3"]):
            item = chat(leaf=bad)
            _, _, turns = grok.conversation_turns(item)
            self.assertEqual([t["id"] for t in turns], ["r1", "r2", "r3"])

    def test_naive_iso_is_utc_whatever_the_host_zone(self):
        old = os.environ.get("TZ")
        try:
            os.environ["TZ"] = "Asia/Makassar"
            if hasattr(__import__("time"), "tzset"):
                __import__("time").tzset()
            self.assertEqual(grok._iso("2023-11-14T22:13:20"), "2023-11-14T22:13:20Z")
        finally:
            if old is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = old
            hasattr(__import__("time"), "tzset") and __import__("time").tzset()


if __name__ == "__main__":
    unittest.main()
