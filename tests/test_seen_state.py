"""seen_state 테스트 — 만료·복구·왕복 저장."""
import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import date

import seen_state

TODAY = date(2026, 9, 14)


class SeenStateTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = os.path.join(self.dir.name, "state", "seen.json")

    def write(self, text):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(text)

    def test_missing_file_starts_empty(self):
        self.assertEqual(seen_state.load(self.path, TODAY), seen_state.empty_state())

    def test_broken_file_starts_empty(self):
        self.write("{깨진 파일")
        with contextlib.redirect_stderr(io.StringIO()):
            state = seen_state.load(self.path, TODAY)
        self.assertEqual(state, seen_state.empty_state())

    def test_expiry_per_kind(self):
        self.write(json.dumps({
            "version": 1,
            "urls": {"https://a": "2026-09-10", "https://old": "2026-09-01"},
            "names": {"신규": "2026-09-01", "옛것": "2026-08-01"},
            "apps": {"111": "2026-06-01", "222": "2026-01-01"},
        }, ensure_ascii=False))
        state = seen_state.load(self.path, TODAY)
        self.assertEqual(list(state["urls"]), ["https://a"])
        self.assertEqual(list(state["names"]), ["신규"])
        self.assertEqual(list(state["apps"]), ["111"])

    def test_mark_and_save_roundtrip(self):
        state = seen_state.empty_state()
        seen_state.mark(state, "urls", ["https://a"], TODAY)
        seen_state.mark(state, "names", ["빨래 톡!"], TODAY)
        seen_state.save(self.path, state)
        reloaded = seen_state.load(self.path, TODAY)
        self.assertTrue(seen_state.is_seen(reloaded, "urls", "https://a"))
        self.assertTrue(seen_state.is_seen(reloaded, "names", "빨래톡"))
        self.assertFalse(seen_state.is_seen(reloaded, "names", "다른앱"))
        self.assertFalse(seen_state.is_seen(reloaded, "urls", ""))

    def test_name_key_normalizes(self):
        self.assertEqual(seen_state.name_key(" 빨래-톡 (Beta) "), "빨래톡beta")


if __name__ == "__main__":
    unittest.main()
