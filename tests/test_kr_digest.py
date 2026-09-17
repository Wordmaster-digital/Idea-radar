"""kr_digest 테스트 — 정리·병합·선정·렌더·조립을 확인한다."""
import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch
from test_idea_ladder import fake_client

import fixtures
import idea_ladder
import kr_digest
import seen_state

NOW = fixtures.NOW
TODAY = date(2026, 9, 14)


class GroupTests(unittest.TestCase):
    def test_canonical_url_drops_tracking(self):
        self.assertEqual(
            kr_digest.canonical_url("https://a.example/b?utm_source=x&id=7&fbclid=z#c"),
            "https://a.example/b?id=7")
        self.assertEqual(kr_digest.canonical_url("javascript:alert(1)"), "")

    def test_title_key_ignores_brackets_and_punctuation(self):
        self.assertEqual(kr_digest.title_key("[단독] '빨래톡' 출시!"),
                         kr_digest.title_key("빨래톡 출시"))

    def test_groups_same_title_and_prefers_media_url(self):
        items = [fixtures.news_item("빨래톡 출시", "https://news.google.com/x"),
                 fixtures.news_item("[단독] 빨래톡 출시", "https://media.example/1",
                                    outlet="가상신문", source="media", desc="설명")]
        groups = kr_digest.group_items(items, seen_state.empty_state())
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["url"], "https://media.example/1")
        self.assertEqual(groups[0]["outlets"], {"가상일보", "가상신문"})
        self.assertEqual(len(groups[0]["urls"]), 2)

    def test_noise_dropped_but_insight_kept(self):
        items = [fixtures.news_item("[부고] 아무개 별세", "https://a.example/1"),
                 fixtures.news_item("인사이트 데이 참가 앱 등장", "https://a.example/2")]
        titles = [g["title"] for g in kr_digest.group_items(items, seen_state.empty_state())]
        self.assertEqual(titles, ["인사이트 데이 참가 앱 등장"])

    def test_seen_url_and_app_dropped(self):
        state = seen_state.empty_state()
        seen_state.mark(state, "urls", ["https://a.example/1"], TODAY)
        seen_state.mark(state, "apps", ["111"], TODAY)
        items = [fixtures.news_item("새 앱 등장", "https://a.example/1"),
                 fixtures.app_item("빨래톡", "111")]
        self.assertEqual(kr_digest.group_items(items, state), [])

    def test_news_cap_keeps_apps(self):
        items = [fixtures.news_item(f"앱{n} 등장", f"https://a.example/{n}", hours_ago=n % 20 + 1)
                 for n in range(kr_digest.MAX_NEWS + 5)]
        items.append(fixtures.app_item())
        groups = kr_digest.group_items(items, seen_state.empty_state())
        self.assertEqual(len(groups), kr_digest.MAX_NEWS + 1)
        self.assertTrue(any(g["source"] == "appstore" for g in groups))


class MergeTests(unittest.TestCase):
    def setUp(self):
        self.state = seen_state.empty_state()
        self.groups = [
            {**fixtures.news_item("빨래톡 등장", "https://a.example/1"),
             "urls": ["https://a.example/1"], "outlets": {"가상일보"}},
            {**fixtures.news_item("빨래 톡 매출 1억", "https://a.example/2", outlet="가상신문"),
             "urls": ["https://a.example/2"], "outlets": {"가상신문"}},
            {**fixtures.app_item("초록집", "222", rank=5),
             "urls": ["https://apps.apple.com/kr/app/id222"], "outlets": {"앱스토어 무료 #5"}},
        ]

    def test_merges_by_name_and_sorts(self):
        cards = [fixtures.card(0, "빨래톡"), fixtures.card(1, "빨래 톡", traction="매출 1억"),
                 fixtures.card(2, "초록집")]
        merged = kr_digest.merge_cards(self.groups, cards, self.state)
        self.assertEqual([c["name"] for c in merged], ["빨래톡", "초록집"])
        self.assertEqual(merged[0]["outlets"], {"가상일보", "가상신문"})
        self.assertEqual(merged[0]["traction"], "매출 1억")

    def test_drops_rejected_and_seen_names(self):
        seen_state.mark(self.state, "names", ["초록집"], TODAY)
        cards = [fixtures.card(0, "빨래톡", keep=False), fixtures.card(1, "빨래톡"),
                 fixtures.card(2, "초록집")]
        merged = kr_digest.merge_cards(self.groups, cards, self.state)
        self.assertEqual([c["name"] for c in merged], ["빨래톡"])


class SplitTests(unittest.TestCase):
    def test_three_full_and_five_stops(self):
        ideas = ([fixtures.idea(n, "보류", f"보류{n}") for n in range(2)]
                 + [fixtures.idea(2, "GO", "GO0")]
                 + [fixtures.idea(n, "STOP", f"STOP{n}") for n in range(3, 9)])
        full, stops = kr_digest.split_ideas(ideas)
        self.assertEqual([i["title"] for i in full], ["GO0", "보류0", "보류1"])
        self.assertEqual(len(stops), 5)


class RenderTests(unittest.TestCase):
    def selected(self):
        return [{**fixtures.card(0, "빨래톡", traction="가입자 2만"),
                 "url": "https://a.example/1", "urls": ["https://a.example/1"],
                 "outlets": {"가상일보", "가상신문"}, "chart_rank": None,
                 "published": NOW, "source": "gnews"}]

    def test_full_idea_block_has_every_field(self):
        lines = kr_digest.render_ideas("2026-09-16", [fixtures.idea(0)], [], self.selected())
        text = "\n".join(lines)
        for token in ("추가할 축", "정해주는 행동", "불편 장면", "예상 밖", "공개 데이터",
                      "2주 MVP", "타이밍", "지불자", "함정", "국내 중복", "판정 이유"):
            self.assertIn(token, text)
        self.assertIn("[빨래톡](https://a.example/1)", text)

    def test_only_stops_gets_notice(self):
        lines = kr_digest.render_ideas("2026-09-16", [], [fixtures.idea(0, "STOP")],
                                       self.selected())
        text = "\n".join(lines)
        self.assertIn("GO·보류 판정 아이디어가 없습니다", text)
        self.assertIn("❌", text)

    def test_no_ideas_notice(self):
        text = "\n".join(kr_digest.render_ideas("2026-09-16", [], [], self.selected()))
        self.assertIn("기준을 통과한 아이디어가 없습니다", text)

    def test_card_list_shows_counts_and_overflow(self):
        cards = [{**fixtures.card(n, f"앱{n}"), "url": f"https://a.example/{n}", "urls": [],
                  "outlets": {"가상일보"}, "chart_rank": None, "published": NOW,
                  "source": "gnews"} for n in range(kr_digest.MAX_LIST + 3)]
        text = "\n".join(kr_digest.render_cards(24, cards))
        self.assertIn(f"({len(cards)}건)", text)
        self.assertIn("외 3건", text)


class ChunkTests(unittest.TestCase):
    def test_chunks_stay_under_limit(self):
        lines = [f"· 줄 {n} " + "가" * 120 for n in range(40)]
        chunks = kr_digest.chunk_lines(lines)
        self.assertTrue(all(len(c) <= kr_digest.CHUNK_LIMIT for c in chunks))
        self.assertEqual("\n".join(lines), "\n".join(chunks))

    def test_single_long_line_is_split(self):
        chunks = kr_digest.chunk_lines(["가" * (kr_digest.CHUNK_LIMIT + 50)])
        self.assertEqual(len(chunks), 2)


class SendTests(unittest.TestCase):
    def test_body_blocks_mentions(self):
        captured = {}

        @contextlib.contextmanager
        def opener(req, timeout=0):
            captured["body"] = json.loads(req.data.decode("utf-8"))
            yield SimpleNamespace(status=204)

        kr_digest.send_discord("https://discord.example/hook", "@everyone 알림", opener)
        self.assertEqual(captured["body"]["allowed_mentions"], {"parse": []})
        self.assertEqual(captured["body"]["flags"], 4)
        self.assertEqual(captured["body"]["content"], "@everyone 알림")


class ReportTests(unittest.TestCase):
    def build(self, items, client, state=None):
        with contextlib.redirect_stderr(io.StringIO()):
            return kr_digest.build_report(
                items, state or seen_state.empty_state(), hours=24, today=TODAY,
                client=client, evidence_fn=lambda card: card.setdefault("evidence", []))

    def test_empty_day(self):
        lines, record = self.build([], None)
        self.assertIn("신규 아이템이 없습니다", "\n".join(lines))
        self.assertEqual(record, {"urls": [], "apps": [], "names": []})

    def test_without_client_lists_raw_items(self):
        lines, record = self.build([fixtures.news_item("빨래톡 등장", "https://a.example/1")], None)
        text = "\n".join(lines)
        self.assertIn(kr_digest.NOTE_NO_LLM, text)
        self.assertIn("빨래톡 등장", text)
        self.assertEqual(record["urls"], [])

    def test_card_failure_falls_back(self):
        items = [fixtures.news_item("빨래톡 등장", "https://a.example/1")]
        with patch.object(idea_ladder, "make_cards", side_effect=idea_ladder.LadderError("실패")):
            lines, record = self.build(items, object())
        self.assertIn(kr_digest.NOTE_CARD_FAIL, "\n".join(lines))
        self.assertEqual(record["urls"], [])

    def test_idea_failure_keeps_card_list(self):
        items = [fixtures.news_item("빨래톡 등장", "https://a.example/1")]
        with patch.object(idea_ladder, "make_cards", return_value=[fixtures.card(0, "빨래톡")]), \
             patch.object(idea_ladder, "make_ideas", side_effect=idea_ladder.LadderError("실패")):
            lines, record = self.build(items, object())
        text = "\n".join(lines)
        self.assertIn(kr_digest.NOTE_IDEA_FAIL, text)
        self.assertIn("빨래톡", text)
        self.assertEqual(record["urls"], [])

    def test_success_records_keys(self):
        items = [fixtures.news_item("빨래톡 등장", "https://a.example/1"), fixtures.app_item()]
        with patch.object(idea_ladder, "make_cards",
                          return_value=[fixtures.card(0, "빨래톡"), fixtures.card(1, "빨래앱")]), \
             patch.object(idea_ladder, "make_ideas", return_value=[fixtures.idea(0)]):
            lines, record = self.build(items, object())
        text = "\n".join(lines)
        self.assertIn("보완 아이디어", text)
        self.assertIn("국내 신규 아이템", text)
        self.assertEqual(record["apps"], ["111"])
        self.assertIn("https://a.example/1", record["urls"])
        self.assertIn("빨래톡", record["names"])


class MainTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.state_path = os.path.join(self.dir.name, "seen.json")
        self.sent = []

    def fetcher(self, url):
        if "news.google.com" in url:
            return fixtures.GNEWS_XML
        if "itunes.apple.com" in url:
            return fixtures.APPSTORE_JSON
        return fixtures.MEDIA_XML

    def run_main(self, argv, webhook="https://discord.example/hook", fetcher=None, fail_send=False,
                 llm=False, client_factory=None):
        @contextlib.contextmanager
        def opener(req, timeout=0):
            if fail_send:
                raise OSError("발송 실패")
            self.sent.append(json.loads(req.data.decode("utf-8")))
            yield SimpleNamespace(status=204)

        env = {"IDEA_LLM_MODE": "codex" if llm else "off", "DISCORD_WEBHOOK_URL": webhook}
        with patch.dict(os.environ, env), patch.object(idea_ladder, "is_available", return_value=llm), \
             contextlib.redirect_stderr(io.StringIO()), \
             contextlib.redirect_stdout(io.StringIO()) as out:
            code = kr_digest.main(argv, now=NOW, fetcher=fetcher or self.fetcher,
                                  client_factory=client_factory,
                                  opener=opener, sleep=lambda seconds: None,
                                  evidence_fn=lambda card: card.setdefault("evidence", []))
        return code, out.getvalue()

    def test_dry_run_prints_without_saving_state(self):
        code, text = self.run_main(["--dry-run", "--state", self.state_path])
        self.assertEqual(code, 0)
        self.assertIn("국내 신규 아이템", text)
        self.assertEqual(self.sent, [])
        self.assertFalse(os.path.exists(self.state_path))

    def test_missing_webhook_fails(self):
        code, _ = self.run_main(["--state", self.state_path], webhook="")
        self.assertEqual(code, 1)

    def test_no_llm_flag_skips_client_even_when_available(self):
        with patch.object(idea_ladder, "make_client") as factory:
            code, _ = self.run_main(["--dry-run", "--no-llm", "--state", self.state_path], llm=True)
        self.assertEqual(code, 0)
        factory.assert_not_called()
        self.assertEqual(self.sent, [])

    def test_all_sources_failing_returns_error(self):
        def boom(url):
            raise OSError("조회 실패")

        code, _ = self.run_main(["--state", self.state_path], fetcher=boom)
        self.assertEqual(code, 1)
        self.assertEqual(self.sent, [])

    def test_send_saves_state(self):
        code, _ = self.run_main(["--state", self.state_path])
        self.assertEqual(code, 0)
        self.assertTrue(self.sent)
        self.assertTrue(os.path.exists(self.state_path))

    def test_send_failure_keeps_state_unsaved(self):
        code, _ = self.run_main(["--state", self.state_path], fail_send=True)
        self.assertEqual(code, 1)
        self.assertFalse(os.path.exists(self.state_path))

    def assert_no_seen_keys(self):
        with open(self.state_path, encoding="utf-8") as saved:
            self.assertEqual(json.load(saved), seen_state.empty_state())

    def test_disabled_llm_never_initializes_client(self):
        with patch.object(idea_ladder, "make_client") as factory:
            code, _ = self.run_main(["--state", self.state_path])
        self.assertEqual(code, 0)
        factory.assert_not_called()
        self.assertIn(kr_digest.NOTE_NO_LLM, self.sent[0]["content"])
        self.assert_no_seen_keys()

    def test_client_initialization_failure_sends_raw_and_records_nothing(self):
        with patch.object(idea_ladder, "make_client",
                          side_effect=idea_ladder.LadderError("초기화 실패")):
            code, _ = self.run_main(["--state", self.state_path], llm=True)
        self.assertEqual(code, 0)
        self.assertIn(kr_digest.NOTE_CLIENT_FAIL, self.sent[0]["content"])
        self.assert_no_seen_keys()

    def test_real_card_error_path_sends_raw_and_records_nothing(self):
        for kwargs in ({"error": RuntimeError("API 실패")}, {"text": "[]"},
                       {"text": '{"cards": []}'}, {"status": "incomplete"}, {"refusal": True}):
            with self.subTest(kwargs=kwargs):
                client, _ = fake_client(**kwargs)
                self.sent.clear()
                code, _ = self.run_main(["--state", self.state_path], llm=True,
                                         client_factory=lambda: client)
                self.assertEqual(code, 0)
                self.assertIn(kr_digest.NOTE_CARD_FAIL, self.sent[0]["content"])
                self.assert_no_seen_keys()

    def test_codex_success_runs_both_stages_and_records_keys(self):
        client, responses = fake_client()
        original_generate = responses.generate

        def generate(system, payload, schema, effort):
            if isinstance(payload, list):
                data = {"cards": [fixtures.card(i, f"앱{i}") for i in range(len(payload))]}
            else:
                data = {"ideas": [fixtures.idea(i) for i in range(len(payload["items"]))]}
            responses.text = json.dumps(data)
            return original_generate(system, payload, schema, effort)

        responses.generate = generate
        code, _ = self.run_main(["--state", self.state_path], llm=True,
                                 client_factory=lambda: client)
        self.assertEqual(code, 0)
        self.assertEqual(len(responses.calls), 2)
        self.assertIn("보완 아이디어", self.sent[0]["content"])
        state = seen_state.load(self.state_path, NOW.astimezone(kr_digest.KST).date())
        self.assertTrue(state["urls"])
        self.assertTrue(state["names"])

    def test_codex_idea_failure_keeps_cards_without_recording(self):
        client, responses = fake_client()
        original_generate = responses.generate

        def generate(system, payload, schema, effort):
            if isinstance(payload, list):
                responses.text = json.dumps({"cards": [
                    fixtures.card(i, f"앱{i}") for i in range(len(payload))]})
            else:
                responses.status = "incomplete"
            return original_generate(system, payload, schema, effort)

        responses.generate = generate
        code, _ = self.run_main(["--state", self.state_path], llm=True,
                                 client_factory=lambda: client)
        self.assertEqual(code, 0)
        self.assertIn(kr_digest.NOTE_IDEA_FAIL, self.sent[0]["content"])
        self.assertIn("앱0", self.sent[0]["content"])
        self.assert_no_seen_keys()


if __name__ == "__main__":
    unittest.main()
