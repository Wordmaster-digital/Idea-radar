"""실제 SDK + 메모리 HTTP 응답으로 직렬화·파싱·재시도 정책을 검증한다."""
import contextlib
import io
import json
import os
import unittest
from unittest.mock import patch

import httpx
from openai import OpenAI

import fixtures
import idea_ladder
from test_idea_ladder import ITEMS


def response_body(*, status="completed", refusal=False):
    content = ({"type": "refusal", "refusal": "거절"} if refusal else
               {"type": "output_text", "text": json.dumps({"cards": [fixtures.card(0, "앱")]}),
                "annotations": []})
    return {
        "id": "resp_test", "object": "response", "created_at": 1, "model": "gpt-5.6-sol",
        "status": status, "error": None,
        "incomplete_details": {"reason": "max_output_tokens"} if status == "incomplete" else None,
        "output": [
            {"id": "rs_test", "type": "reasoning", "summary": []},
            {"id": "msg_test", "type": "message", "status": "completed",
             "role": "assistant", "content": [content]}],
        "usage": {"input_tokens": 100, "output_tokens": 200, "total_tokens": 300,
                  "input_tokens_details": {"cached_tokens": 0},
                  "output_tokens_details": {"reasoning_tokens": 100}},
    }


class SDKTransportTests(unittest.TestCase):
    def run_request(self, handler):
        # 실제 네트워크 없이 SDK의 재시도와 응답 객체 변환을 통과시킨다.
        with httpx.Client(transport=httpx.MockTransport(handler)) as http_client, \
             OpenAI(api_key="test-key", base_url="https://api.openai.com/v1",
                    http_client=http_client, timeout=idea_ladder.REQUEST_TIMEOUT,
                    max_retries=idea_ladder.MAX_RETRIES) as client, \
             patch("openai._base_client.time.sleep"), \
             patch.dict(os.environ, {"IDEA_MODEL": ""}), \
             contextlib.redirect_stderr(io.StringIO()):
            return idea_ladder.make_cards(client, ITEMS)

    def test_real_sdk_request_and_output_text_after_reasoning_item(self):
        requests = []

        def handler(request):
            requests.append(request)
            return httpx.Response(200, json=response_body())

        cards = self.run_request(handler)
        self.assertEqual(cards[0]["name"], "앱")
        self.assertEqual(str(requests[0].url), "https://api.openai.com/v1/responses")
        self.assertEqual(requests[0].method, "POST")
        self.assertEqual(requests[0].headers["authorization"], "Bearer test-key")
        body = json.loads(requests[0].content)
        self.assertEqual(body["text"]["format"]["schema"], idea_ladder.CARD_SCHEMA)
        self.assertTrue(body["text"]["format"]["strict"])
        self.assertEqual(body["reasoning"], {"effort": "low"})
        self.assertFalse(body["store"])
        self.assertEqual(requests[0].extensions["timeout"]["read"], 120.0)

    def test_transient_status_retries_same_model_then_succeeds(self):
        for status in (408, 409, 429, 500, 503):
            calls = []

            def handler(request):
                calls.append(json.loads(request.content))
                if len(calls) < 3:
                    return httpx.Response(status, json={"error": {"message": "temporary"}})
                return httpx.Response(200, json=response_body())

            with self.subTest(status=status):
                self.assertEqual(len(self.run_request(handler)), 1)
                self.assertEqual(len(calls), 3)
                self.assertTrue(all(call["model"] == "gpt-5.6-sol" for call in calls))
                self.assertEqual(calls[0], calls[1])
                self.assertEqual(calls[1], calls[2])

    def test_nonretryable_status_fails_after_one_request(self):
        for status in (400, 401, 403, 404, 422):
            calls = []

            def handler(request):
                calls.append(request)
                return httpx.Response(status, json={"error": {"message": "sensitive-key"}})

            with self.subTest(status=status), self.assertRaises(idea_ladder.LadderError) as caught:
                self.run_request(handler)
            self.assertEqual(len(calls), 1)
            self.assertIn(str(status), str(caught.exception))
            self.assertNotIn("sensitive-key", str(caught.exception))

    def test_retries_exhausted_becomes_ladder_error(self):
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(503, json={"error": {"message": "unavailable"}})

        with self.assertRaises(idea_ladder.LadderError):
            self.run_request(handler)
        self.assertEqual(len(calls), 3)

    def test_timeout_and_connection_failures_are_bounded(self):
        for error_type in (httpx.ReadTimeout, httpx.ConnectError):
            calls = []

            def handler(request):
                calls.append(request)
                raise error_type("network error", request=request)

            with self.subTest(error_type=error_type), self.assertRaises(idea_ladder.LadderError):
                self.run_request(handler)
            self.assertEqual(len(calls), 3)

    def test_incomplete_and_refusal_do_not_retry_or_use_partial_json(self):
        for kwargs in ({"status": "incomplete"}, {"refusal": True}):
            calls = []

            def handler(request):
                calls.append(request)
                return httpx.Response(200, json=response_body(**kwargs))

            with self.subTest(kwargs=kwargs), self.assertRaises(idea_ladder.LadderError):
                self.run_request(handler)
            self.assertEqual(len(calls), 1)
