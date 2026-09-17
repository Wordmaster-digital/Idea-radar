"""수동 해외 다이제스트도 동일 Codex 구독 경로를 사용하고 실패 시 원본을 보존한다."""
import contextlib
import copy
import io
import json
import unittest
from unittest.mock import patch

import idea_ladder
import llm_enrich
from test_idea_ladder import fake_client


class EnrichTests(unittest.TestCase):
    def setUp(self):
        self.items = [{"title": "Sample", "url": "https://example.com", "extra": "detail"}]

    def run_with_response(self, fn, rows):
        client, responses = fake_client(text=json.dumps({"rows": rows}))
        with patch.object(idea_ladder, "make_client", return_value=client), \
             contextlib.redirect_stderr(io.StringIO()):
            result = fn(self.items)
        self.assertEqual(len(responses.calls), 1)
        self.assertFalse(responses.calls[0]["schema"]["additionalProperties"])
        return result

    def test_translation(self):
        result = self.run_with_response(llm_enrich.translate_batch,
                                       [{"i": 0, "ko": "한국어", "kw": ["키워드"], "cat": "기타"}])
        self.assertEqual(result[0]["ko"], "한국어")
        self.assertEqual(result[0]["kw"], ["키워드"])

    def test_judgment(self):
        result = self.run_with_response(llm_enrich.judge_batch, [{"i": 0, "v": "불명", "why": "근거 없음"}])
        self.assertEqual(result[0]["verdict"], "불명")

    def test_shortlist(self):
        result = self.run_with_response(llm_enrich.shortlist,
                                       [{"i": 0, "what": "기능", "axis": "축", "who": "사용자", "risk": "위험"}])
        self.assertEqual(result[0]["url"], self.items[0]["url"])

    def test_shortlist_can_be_empty(self):
        self.assertEqual(self.run_with_response(llm_enrich.shortlist, []), [])

    def test_api_failure_preserves_original(self):
        original = copy.deepcopy(self.items)
        with patch.object(idea_ladder, "make_client", side_effect=idea_ladder.LadderError("실패")), \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(llm_enrich.translate_batch(self.items), original)
            self.assertEqual(llm_enrich.judge_batch(self.items), original)
            self.assertEqual(llm_enrich.shortlist(self.items), [])

    def test_bad_translation_does_not_partially_mutate_items(self):
        original = copy.deepcopy(self.items)
        self.run_with_response(llm_enrich.translate_batch,
                               [{"i": 0, "ko": "한국어", "kw": [1], "cat": "기타"}])
        self.assertEqual(self.items, original)

    def test_empty_input_never_calls_api(self):
        with patch.object(idea_ladder, "make_client") as factory:
            for fn in (llm_enrich.translate_batch, llm_enrich.judge_batch, llm_enrich.shortlist):
                self.assertEqual(fn([]), [])
        factory.assert_not_called()
