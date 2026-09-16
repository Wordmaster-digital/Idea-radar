"""idea_ladder 테스트 — 가짜 클라이언트로 요청 인자와 응답 처리를 확인한다."""
import contextlib
import io
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import fixtures
import idea_ladder


class FakeStream:
    def __init__(self, message):
        self.message = message

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self.message


class FakeMessages:
    def __init__(self, text="{}", stop_reason="end_turn", error=None):
        self.text, self.stop_reason, self.error, self.calls = text, stop_reason, error, []

    def stream(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return FakeStream(SimpleNamespace(
            stop_reason=self.stop_reason,
            content=[SimpleNamespace(type="text", text=self.text)]))


def fake_client(**kwargs):
    messages = FakeMessages(**kwargs)
    return SimpleNamespace(beta=SimpleNamespace(messages=messages)), messages


ITEMS = [{"title": "빨래톡 등장", "desc": "", "outlets": {"가상일보"}, "chart_rank": None}]
CARDS_JSON = json.dumps({"cards": [
    fixtures.card(0, "빨래톡"),
    {**fixtures.card(1, "범위밖"), "i": 9},
    {"i": 0, "keep": True},
]}, ensure_ascii=False)


class RequestTests(unittest.TestCase):
    def test_default_model_enables_fallbacks(self):
        client, messages = fake_client(text=CARDS_JSON)
        with patch.dict(os.environ, {"IDEA_MODEL": ""}), contextlib.redirect_stderr(io.StringIO()):
            idea_ladder.make_cards(client, ITEMS)
        sent = messages.calls[0]
        self.assertEqual(sent["model"], "claude-opus-5")
        self.assertEqual(sent["fallbacks"], "default")
        self.assertEqual(sent["betas"], ["server-side-fallback-2026-07-01"])
        self.assertEqual(sent["output_config"]["effort"], "low")
        self.assertEqual(sent["output_config"]["format"]["type"], "json_schema")

    def test_other_model_skips_fallbacks(self):
        client, messages = fake_client(text=CARDS_JSON)
        with patch.dict(os.environ, {"IDEA_MODEL": "claude-sonnet-5"}), \
             contextlib.redirect_stderr(io.StringIO()):
            idea_ladder.make_cards(client, ITEMS)
        self.assertNotIn("fallbacks", messages.calls[0])
        self.assertNotIn("betas", messages.calls[0])


class CardTests(unittest.TestCase):
    def test_keeps_only_valid_rows(self):
        client, _ = fake_client(text=CARDS_JSON)
        with contextlib.redirect_stderr(io.StringIO()):
            cards = idea_ladder.make_cards(client, ITEMS)
        self.assertEqual([c["name"] for c in cards], ["빨래톡"])

    def test_refusal_raises(self):
        client, _ = fake_client(text=CARDS_JSON, stop_reason="refusal")
        with self.assertRaises(idea_ladder.LadderError):
            idea_ladder.make_cards(client, ITEMS)

    def test_api_error_raises(self):
        client, _ = fake_client(error=RuntimeError("서버 오류"))
        with self.assertRaises(idea_ladder.LadderError):
            idea_ladder.make_cards(client, ITEMS)

    def test_broken_json_raises(self):
        client, _ = fake_client(text="설명만 있고 JSON이 아님")
        with self.assertRaises(idea_ladder.LadderError):
            idea_ladder.make_cards(client, ITEMS)


class IdeaTests(unittest.TestCase):
    def test_payload_and_effort(self):
        body = json.dumps({"ideas": [fixtures.idea(0)]}, ensure_ascii=False)
        client, messages = fake_client(text=body)
        cards = [{**fixtures.card(0, "빨래톡"), "outlets": {"가상일보", "가상신문"},
                  "evidence": ["[앱] 빨래앱"]}]
        ideas = idea_ladder.make_ideas(client, cards, "2026-09-16")
        self.assertEqual(len(ideas), 1)
        sent = messages.calls[0]
        self.assertEqual(sent["output_config"]["effort"], "high")
        payload = json.loads(sent["messages"][0]["content"])
        self.assertEqual(payload["today"], "2026-09-16")
        self.assertEqual(payload["items"][0]["outlets"], 2)
        self.assertEqual(payload["items"][0]["evidence"], ["[앱] 빨래앱"])


if __name__ == "__main__":
    unittest.main()
