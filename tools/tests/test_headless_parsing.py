"""Unit tests for tools/headless_test.py log parsing (stdlib unittest).

  python -m unittest discover -s tools/tests -v
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import headless_test as ht  # noqa: E402
import ks_common as ks  # noqa: E402

SAMPLE_LOG = """\
[19:53:31] [main/ERROR] (HkeyPerformanceDataUtil) Unable to locate English counter names in registry Perflib 009. Counters may need to be rebuilt:
[19:53:37] [main/INFO] (karstscape) Karstscape initialized
[19:53:40] [Server thread/WARN] (Minecraft) **** SERVER IS RUNNING IN OFFLINE/INSECURE MODE!
[19:53:41] [Worker-Main-3/WARN] (Minecraft) Something took 123 ms at 0x7ffd1234abcd
java.lang.RuntimeException: boom
\tat foo.Bar.baz(Bar.java:1)
[19:53:42] [Server thread/INFO] (Minecraft) Done (2.689s)! For help, type "help"
[19:53:43] [Server thread/ERROR] (Minecraft) Real problem 42
"""


class ParseTests(unittest.TestCase):
    def test_parse_entries_and_continuations(self):
        entries = ht.parse_log(SAMPLE_LOG)
        self.assertEqual(len(entries), 6)
        self.assertEqual(entries[0]["level"], "ERROR")
        self.assertEqual(entries[0]["logger"], "HkeyPerformanceDataUtil")
        self.assertEqual(entries[3]["thread"], "Worker-Main-3")
        self.assertEqual(entries[3]["extra"], ["java.lang.RuntimeException: boom", "\tat foo.Bar.baz(Bar.java:1)"])

    def test_vanilla_format_without_logger(self):
        e = ht.parse_log("[19:45:43] [Server thread/INFO]: Done (1.755s)! For help, type \"help\"\n")
        self.assertEqual(len(e), 1)
        self.assertEqual(e[0]["logger"], "")
        self.assertTrue(e[0]["msg"].startswith("Done ("))

    def test_done_regex(self):
        self.assertTrue(ht.DONE_RE.search('[19:53:42] [Server thread/INFO] (Minecraft) Done (2.689s)! For help, type "help"'))
        self.assertTrue(ht.DONE_RE.search('Done (12,345s)! For help'))
        self.assertFalse(ht.DONE_RE.search("Done preparing level"))

    def test_normalize(self):
        self.assertEqual(ht.normalize("Something took 123 ms at 0x7ffd1234abcd"), "Something took # ms at <HEX>")
        self.assertEqual(ht.normalize("id 123e4567-e89b-12d3-a456-426614174000 done"), "id <UUID> done")
        self.assertEqual(ht.normalize(f"file {ks.ROOT}\\x.txt"), "file <ROOT>\\x.txt")
        self.assertEqual(ht.normalize("Perflib 009. Error 0x7d0"), ht.normalize("Perflib 010. Error 0x7d1"))

    def test_analyze(self):
        allow = [re.compile(r"^ERROR \(HkeyPerformanceDataUtil\)")]
        baseline = {"WARN (Minecraft) **** SERVER IS RUNNING IN OFFLINE/INSECURE MODE!"}
        a = ht.analyze(SAMPLE_LOG, ["WARNING: something jvm"], baseline, [], allow)
        self.assertEqual(len(a["allowed_errors"]), 1)
        self.assertEqual([e["key"] for e in a["errors"]], ["ERROR (Minecraft) Real problem #"])
        self.assertIn("WARN (Minecraft) Something took # ms at <HEX>", a["new_warns"])
        self.assertIn("jvm: WARNING: something jvm", a["new_warns"])
        self.assertNotIn("WARN (Minecraft) **** SERVER IS RUNNING IN OFFLINE/INSECURE MODE!", a["new_warns"])

    def test_spam_detection(self):
        log = "".join(f"[10:00:{i % 60:02d}] [Server thread/WARN] (Minecraft) Spam {i}\n" for i in range(ht.SPAM_SAME + 1))
        a = ht.analyze(log, [], set(), [], [])
        self.assertEqual(a["spam_warns"], ["WARN (Minecraft) Spam #"])


class PropertiesTests(unittest.TestCase):
    def test_escape(self):
        self.assertEqual(ht.escape_prop_value("minecraft:normal"), "minecraft\\:normal")
        self.assertEqual(ht.escape_prop_value("karstscape:karst_only"), "karstscape\\:karst_only")
        self.assertEqual(ht.escape_prop_value("a=b"), "a\\=b")
        self.assertEqual(ht.escape_prop_value("20260915"), "20260915")

    def test_profile_keys_are_known(self):
        known = ht.known_property_keys()
        self.assertTrue(known, "server_properties_keys_26.3.txt missing")
        props = ht.server_properties({"seed": 1, "level_type": "minecraft:normal", "level_name": "w"})
        self.assertEqual(sorted(k for k in props if k not in known), [])


if __name__ == "__main__":
    unittest.main()
