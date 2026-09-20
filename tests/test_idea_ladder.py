"""Codex 구독 결과 계약과 저하 모드로 전달할 실패를 네트워크 없이 검증한다."""
import contextlib
import io
import json
import unittest

import fixtures
import idea_ladder


class FakeClient:
    def __init__(self, text="{}", status="completed", refusal=False, error=None):
        self.text, self.status, self.refusal, self.error = text, status, refusal, error
        self.calls = []

    def generate(self, system, payload, schema, effort):
        self.calls.append(dict(system=system, payload=payload, schema=schema, effort=effort))
        if self.error or self.refusal or self.status != "completed":
            raise idea_ladder.LadderError("Codex 분석 실패")
        try:
            return json.loads(self.text)
        except ValueError:
            raise idea_ladder.LadderError("Codex JSON 오류") from None


def fake_client(**kwargs):
    client = FakeClient(**kwargs)
    return client, client


ITEMS = [{"title": "빨래톡 등장", "desc": "", "outlets": {"가상일보"}, "chart_rank": None}]
CARDS_JSON = json.dumps({"cards": [
    fixtures.card(0, "빨래톡"), fixtures.card(9, "범위밖"), {"i": 0, "keep": True},
]}, ensure_ascii=False)


class RequestTests(unittest.TestCase):
    def test_card_prompt_schema_payload_and_effort(self):
        client, calls = fake_client(text=CARDS_JSON)
        with contextlib.redirect_stderr(io.StringIO()):
            idea_ladder.make_cards(client, ITEMS)
        sent = calls.calls[0]
        self.assertEqual(sent["effort"], "low")
        self.assertEqual(sent["schema"], idea_ladder.CARD_SCHEMA)
        self.assertEqual(sent["system"], idea_ladder.CARD_SYSTEM)
        self.assertEqual(sent["payload"][0]["title"], ITEMS[0]["title"])


class CardTests(unittest.TestCase):
    def test_rejected_item_uses_only_three_fields_without_losing_input_coverage(self):
        dropped = {"i": 0, "keep": False, "drop_reason": "대기업 발표"}
        client, _ = fake_client(text=json.dumps({"cards": [dropped]}))
        self.assertEqual(idea_ladder.make_cards(client, ITEMS), [dropped])
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(idea_ladder.LadderError):
            idea_ladder.make_cards(client, ITEMS * 2)

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

    def test_client_error_propagates_without_retry(self):
        error = RuntimeError("sensitive-key")
        error.status_code = 401
        client, responses = fake_client(error=error)
        with self.assertRaises(idea_ladder.LadderError) as caught:
            idea_ladder.make_cards(client, ITEMS)
        self.assertEqual(len(responses.calls), 1)
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
        self.assertEqual(sent["effort"], "high")
        self.assertEqual(sent["schema"], idea_ladder.IDEA_SCHEMA)
        payload = sent["payload"]
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
