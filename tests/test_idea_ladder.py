"""Responses API 계약과 저하 모드로 전달할 실패를 네트워크 없이 검증한다."""
import contextlib
import io
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import fixtures
import idea_ladder


class FakeResponses:
    def __init__(self, text="{}", status="completed", refusal=False, error=None):
        self.text, self.status, self.refusal, self.error = text, status, refusal, error
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        content = [SimpleNamespace(type="refusal" if self.refusal else "output_text")]
        return SimpleNamespace(
            status=self.status, error=None, incomplete_details=None,
            output_text=self.text, output=[SimpleNamespace(type="message", content=content)],
            model=kwargs["model"], usage=SimpleNamespace(input_tokens=100, output_tokens=200))


def fake_client(**kwargs):
    responses = FakeResponses(**kwargs)
    return SimpleNamespace(responses=responses), responses


ITEMS = [{"title": "빨래톡 등장", "desc": "", "outlets": {"가상일보"}, "chart_rank": None}]
CARDS_JSON = json.dumps({"cards": [
    fixtures.card(0, "빨래톡"), fixtures.card(9, "범위밖"), {"i": 0, "keep": True},
]}, ensure_ascii=False)


class RequestTests(unittest.TestCase):
    def test_default_model_and_strict_responses_contract(self):
        client, responses = fake_client(text=CARDS_JSON)
        with patch.dict(os.environ, {"IDEA_MODEL": ""}), contextlib.redirect_stderr(io.StringIO()) as log:
            idea_ladder.make_cards(client, ITEMS)
        sent = responses.calls[0]
        self.assertEqual(sent["model"], "gpt-5.6-sol")
        self.assertEqual(sent["reasoning"], {"effort": "low"})
        self.assertEqual(sent["max_output_tokens"], 32000)
        self.assertFalse(sent["store"])
        self.assertEqual(sent["text"]["format"], {
            "type": "json_schema", "name": "idea_radar", "strict": True,
            "schema": idea_ladder.CARD_SCHEMA})
        self.assertEqual(sent["instructions"], idea_ladder.CARD_SYSTEM)
        self.assertEqual(json.loads(sent["input"][0]["content"])[0]["title"], ITEMS[0]["title"])
        self.assertEqual(set(sent), {"model", "reasoning", "max_output_tokens", "store",
                                     "text", "instructions", "input"})
        self.assertIn("input_tokens=100 output_tokens=200", log.getvalue())

    def test_model_override(self):
        client, responses = fake_client(text=CARDS_JSON)
        with patch.dict(os.environ, {"IDEA_MODEL": "  deployment-model  "}), \
             contextlib.redirect_stderr(io.StringIO()):
            idea_ladder.make_cards(client, ITEMS)
        self.assertEqual(responses.calls[0]["model"], "deployment-model")

    def test_make_client_sets_key_timeout_and_sdk_retries(self):
        factory = Mock()
        with patch.dict("sys.modules", {"openai": SimpleNamespace(OpenAI=factory)}), \
             patch.dict(os.environ, {"OPENAI_API_KEY": " test-key "}):
            self.assertIs(idea_ladder.make_client(), factory.return_value)
        factory.assert_called_once_with(api_key="test-key", timeout=120.0, max_retries=2)

    def test_make_client_failure_is_ladder_error(self):
        factory = Mock(side_effect=RuntimeError("sensitive-key"))
        with patch.dict("sys.modules", {"openai": SimpleNamespace(OpenAI=factory)}), \
             self.assertRaises(idea_ladder.LadderError) as caught:
            idea_ladder.make_client()
        self.assertNotIn("sensitive-key", str(caught.exception))

    def test_missing_sdk_is_ladder_error(self):
        with patch.dict("sys.modules", {"openai": None}), self.assertRaises(idea_ladder.LadderError):
            idea_ladder.make_client()

    def test_key_requires_nonblank_openai_value(self):
        for key, expected in [(None, False), ("", False), ("  ", False), (" test ", True)]:
            env = {} if key is None else {"OPENAI_API_KEY": key}
            with self.subTest(key=key), patch.dict(os.environ, env, clear=True):
                self.assertEqual(idea_ladder.has_key(), expected)


class CardTests(unittest.TestCase):
    def test_keeps_only_valid_rows_when_all_inputs_covered(self):
        client, _ = fake_client(text=CARDS_JSON)
        with contextlib.redirect_stderr(io.StringIO()):
            cards = idea_ladder.make_cards(client, ITEMS)
        self.assertEqual([c["name"] for c in cards], ["빨래톡"])

    def test_refusal_raises_even_with_valid_json(self):
        client, _ = fake_client(text=CARDS_JSON, refusal=True)
        with self.assertRaises(idea_ladder.LadderError):
            idea_ladder.make_cards(client, ITEMS)

    def test_noncompleted_status_never_accepts_partial_json(self):
        for status in ("incomplete", "failed", "cancelled", "queued", "in_progress"):
            with self.subTest(status=status), self.assertRaises(idea_ladder.LadderError):
                client, _ = fake_client(text=CARDS_JSON, status=status)
                idea_ladder.make_cards(client, ITEMS)

    def test_api_error_is_sanitized_and_not_retried_by_wrapper(self):
        error = RuntimeError("sensitive-key")
        error.status_code = 401
        client, responses = fake_client(error=error)
        with self.assertRaises(idea_ladder.LadderError) as caught:
            idea_ladder.make_cards(client, ITEMS)
        self.assertEqual(len(responses.calls), 1)
        self.assertIn("401", str(caught.exception))
        self.assertNotIn("sensitive-key", str(caught.exception))

    def test_empty_malformed_and_wrong_envelope_raise(self):
        for text in ("", "  ", "설명만", "null", "[]", "1", "{}", '{"cards": null}',
                     '{"cards": {}}', '{"cards": []}', '{"ideas": []}'):
            with self.subTest(text=text), contextlib.redirect_stderr(io.StringIO()), \
                 self.assertRaises(idea_ladder.LadderError):
                client, _ = fake_client(text=text)
                idea_ladder.make_cards(client, ITEMS)

    def test_invalid_types_enums_indices_and_missing_rows_raise(self):
        for change in ({"i": True}, {"i": -1}, {"i": 2}, {"keep": "false"},
                       {"stage": True}, {"stage": 4}, {"maker": "unknown"},
                       {"name": None}, {"kw": "검색어"}, {"kw": [1]}, {"extra": "x"}):
            with self.subTest(change=change), contextlib.redirect_stderr(io.StringIO()), \
                 self.assertRaises(idea_ladder.LadderError):
                client, _ = fake_client(text=json.dumps({"cards": [{**fixtures.card(0, "앱"), **change}]}))
                idea_ladder.make_cards(client, ITEMS)

    def test_partial_coverage_raises(self):
        client, _ = fake_client(text=json.dumps({"cards": [fixtures.card(0, "앱")]}))
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(idea_ladder.LadderError):
            idea_ladder.make_cards(client, ITEMS * 2)

    def test_duplicate_index_raises(self):
        client, _ = fake_client(text=json.dumps({"cards": [fixtures.card(0, "앱")] * 2}))
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(idea_ladder.LadderError):
            idea_ladder.make_cards(client, ITEMS)

    def test_empty_input_skips_api(self):
        self.assertEqual(idea_ladder.make_cards(None, []), [])
        self.assertEqual(idea_ladder.make_ideas(None, [], "2026-09-16"), [])


class IdeaTests(unittest.TestCase):
    def test_payload_and_effort(self):
        client, responses = fake_client(text=json.dumps({"ideas": [fixtures.idea(0)]}))
        cards = [{**fixtures.card(0, "빨래톡"), "outlets": {"가상일보", "가상신문"},
                  "evidence": ["[앱] 빨래앱"]}]
        with contextlib.redirect_stderr(io.StringIO()):
            ideas = idea_ladder.make_ideas(client, cards, "2026-09-16")
        self.assertEqual(len(ideas), 1)
        sent = responses.calls[0]
        self.assertEqual(sent["reasoning"], {"effort": "high"})
        self.assertEqual(sent["text"]["format"]["schema"], idea_ladder.IDEA_SCHEMA)
        payload = json.loads(sent["input"][0]["content"])
        self.assertEqual(payload["today"], "2026-09-16")
        self.assertEqual(payload["items"][0]["outlets"], 2)
        self.assertEqual(payload["items"][0]["evidence"], ["[앱] 빨래앱"])

    def test_invalid_idea_cannot_reach_renderer(self):
        for change in ({"data_sources": [1]}, {"verdict": "MAYBE"}, {"kr_duplicate": "unknown"}):
            with self.subTest(change=change), contextlib.redirect_stderr(io.StringIO()), \
                 self.assertRaises(idea_ladder.LadderError):
                client, _ = fake_client(text=json.dumps({"ideas": [{**fixtures.idea(0), **change}]}))
                idea_ladder.make_ideas(client, [fixtures.card(0, "앱")], "2026-09-16")


if __name__ == "__main__":
    unittest.main()
