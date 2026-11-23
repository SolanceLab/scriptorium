# Scriptorium — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""The browser extension's files against the engine: the filename encoding and the capture files the extension writes.
The same fixtures are checked by `node --test extension/test`. Run: python3 -m unittest test_extension_fixtures -v"""
import glob
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, "..", "extension", "test", "fixtures")
sys.path.insert(0, os.path.join(HERE, "..", "engine"))
import capture_file as cf  # noqa: E402


class ExtensionFixtures(unittest.TestCase):
    def test_filenames_match_the_javascript_encoder(self):
        with open(os.path.join(FIX, "filenames.json"), encoding="utf-8") as f:
            cases = json.load(f)
        self.assertGreaterEqual(len(cases), 10)
        for c in cases:
            self.assertEqual(cf.capture_name(c["site"], c["id"]), c["name"], c["id"])
            self.assertEqual(cf.parse_name(c["name"]), (c["site"], c["id"]))

    def test_every_capture_the_extension_writes_is_accepted(self):
        paths = sorted(glob.glob(os.path.join(FIX, "*.capture.json")))
        self.assertEqual(len(paths), 3)
        for p in paths:
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
            with tempfile.TemporaryDirectory() as t:
                dest = os.path.join(t, cf.capture_name(data["site"], data["conversation_id"]))
                shutil.copy(p, dest)
                got = cf.read_capture(dest)
            self.assertEqual(got["conversation_id"], data["conversation_id"])
            self.assertEqual([m["text"][:14] for m in got["messages"]], ["hello there fr", "retried answer", "and again"])

    def test_an_incomplete_capture_is_refused(self):
        with open(os.path.join(FIX, "grok-branched.capture.json"), encoding="utf-8") as f:
            data = json.load(f)
        data["complete"] = False
        with tempfile.TemporaryDirectory() as t:
            dest = os.path.join(t, cf.capture_name("grok", data["conversation_id"]))
            with open(dest, "w", encoding="utf-8") as f:
                json.dump(data, f)
            with self.assertRaises(cf.CaptureError):
                cf.read_capture(dest)


if __name__ == "__main__":
    unittest.main()
