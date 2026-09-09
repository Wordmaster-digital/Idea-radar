import contextlib
import io
import json
import unittest
from unittest.mock import Mock, patch

import daily_digest as digest
import llm_enrich
from startup_focus import (DEFAULT_SOURCES, assess, canonical_url, chunk_text,
                           needs_market_check, select_startup_items)


def record(title, extra="", source="HN", url="https://example.org/item", **kw):
    return {"title": title, "extra": extra, "source": source, "url": url,
            "score": 10, "unit": "pt", "age_h": 1, **kw}


class FocusTests(unittest.TestCase):
    def test_customer_problem(self):
        it = record("Ask HN: How do small restaurants handle no-shows?",
                    "Customers cancel late and owners need a less manual workaround")
        self.assertEqual(assess(it)[0], "고객 문제·수요")

    def test_business_models(self):
        self.assertEqual(assess(record("How we found paying customers for our bootstrapped SaaS"))[0], "사업모델·수익화")
        self.assertEqual(assess(record("소상공인 고객을 위한 구독 수익모델 사례", source="플래텀"))[0], "사업모델·수익화")

    def test_primary_founder_case(self):
        title = "Guideline's Path to Product-Market Fit — The Early Decisions That Powered Its Acquisition by Gusto"
        self.assertEqual(assess(record(title, source="First Round Review"))[0], "고객 확보·검증")

    def test_funding_and_launch_news_are_excluded(self):
        for title in ("Startup Acme raises $20M to help restaurants", "소상공인 AI 스타트업 100억 투자 유치",
                      "Retailer launches new shopping app", "AI 스타트업 신규 제품 출시",
                      "Diana Hu Is YC's Newest Managing Partner", "창업센터 업무협약 체결"):
            with self.subTest(title=title):
                self.assertIsNone(assess(record(title, source="Y Combinator")))

    def test_investment_lessons_and_actual_demos_survive(self):
        self.assertIsNotNone(assess(record("창업기업 투자유치 전략과 실패 사례", source="플래텀")))
        it = record("Show HN: I launched a scheduler for restaurant owners", "Helps restaurants manage bookings")
        self.assertIsNotNone(assess(it))

    def test_generic_news_and_empty_product_are_rejected(self):
        for it in (record("New laptop review", "Fast and modern design"),
                   record("Acme AI", source="PH"), record("The history of an operating system")):
            self.assertIsNone(assess(it))

    def test_product_with_customer_and_use_case(self):
        it = record("Booking Buddy", "Helps freelancers manage client bookings", source="PH")
        selected = select_startup_items([it])[0]
        self.assertEqual(selected["info_type"], "서비스·제품 사례")
        self.assertTrue(needs_market_check(selected))

    def test_news_feeds_still_use_same_gate(self):
        items = [record("벤처 기업 투자 유치", source="벤처스퀘어"),
                 record("창업자 고객 확보 전략", source="벤처스퀘어", url="https://example.org/guide")]
        self.assertEqual([it["title"] for it in select_startup_items(items)], ["창업자 고객 확보 전략"])

    def test_dedupe_and_tracking_query(self):
        title = "Founder guide to customer discovery"
        items = [record(title, url="https://example.org/view?utm_source=feed&id=42"),
                 record(title, url="https://example.org/view?id=42#comments")]
        selected = select_startup_items(items)
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["url"], "https://example.org/view?id=42")
        self.assertEqual(canonical_url("javascript:alert(1)"), "")

    def test_default_sources_and_publisher_identity(self):
        self.assertEqual(set(DEFAULT_SOURCES.split(",")), {"hn", "ph", "fr", "yc", "platum", "vsq"})
        names = [feed[1] for feed in digest.FEEDS]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(digest.CATEGORY["플래텀"], "한국")

    def test_no_market_check_for_advice(self):
        item = select_startup_items([record("A founder guide to pricing")])[0]
        with patch.object(digest, "kr_appstore") as search:
            annotated = digest.annotate_kr(item)
        search.assert_not_called()
        self.assertEqual(annotated["kr"], "참고자료")

    def test_empty_market_lookup_is_not_a_confirmed_gap(self):
        item = select_startup_items([record("Booking Buddy", "Helps freelancers manage client bookings", source="PH")])[0]
        with patch.object(digest, "kr_appstore", return_value=[]), patch.object(digest.time, "sleep"):
            self.assertEqual(digest.annotate_kr(item)["kr"], "판정불가")


class PipelineTests(unittest.TestCase):
    def run_main(self, records, *args, llm=False):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(digest, "SOURCES", {"fixture": lambda: records}), \
             patch.object(digest, "summarize", side_effect=lambda it: it), \
             patch.object(digest.sys, "argv", ["daily_digest.py", "--sources", "fixture", "--dry-run", *args]), \
             patch.object(llm_enrich, "has_key", return_value=llm), \
             patch.object(digest, "kr_appstore", return_value=[]), \
             patch.object(digest.time, "sleep"), patch.object(digest, "post_discord") as post, \
             contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = digest.main()
        post.assert_not_called()
        return result, stdout.getvalue(), stderr.getvalue()

    def test_no_llm_filters_before_top_cap(self):
        items = [record("Startup raises $50M", score=999999),
                 record("How founders find paying customers", url="https://example.org/useful")]
        result, text, _ = self.run_main(items, "--top", "1")
        self.assertEqual(result, 0)
        self.assertIn("paying customers", text)
        self.assertNotIn("raises $50M", text)
        self.assertIn("활용:", text)

    def test_news_only_is_successful_noop(self):
        result, text, error = self.run_main([record("Startup raises $50M")])
        self.assertEqual(result, 0)
        self.assertEqual(text, "")
        self.assertIn("발송 생략", error)

    def test_expired_results_are_not_sent(self):
        result, text, _ = self.run_main([record("Founder pricing guide", age_h=200)])
        self.assertEqual(result, 0)
        self.assertEqual(text, "")

    def test_llm_rejection_is_not_published(self):
        def translate(items):
            for item in items:
                item["startup_relevant"] = False
            return items
        with patch.object(llm_enrich, "translate_batch", side_effect=translate), \
             patch.object(llm_enrich, "shortlist", return_value=[]), \
             patch.object(llm_enrich, "judge_batch") as judge:
            result, text, _ = self.run_main([record("How founders find customers")], llm=True)
        self.assertEqual(result, 0)
        self.assertEqual(text, "")
        judge.assert_not_called()

    def test_hn_collects_asks_and_demos_separately(self):
        with patch.object(digest, "fetch_json", return_value={"hits": []}) as fetch:
            digest.src_hn()
        urls = [call.args[0] for call in fetch.call_args_list]
        self.assertEqual(len(urls), 2)
        self.assertTrue(any("tags=ask_hn" in url for url in urls))
        self.assertTrue(any("tags=show_hn" in url for url in urls))

    def test_producthunt_alternate_link_and_date(self):
        xml = '''<feed xmlns="http://www.w3.org/2005/Atom"><entry>
          <title>Booking Buddy</title><link rel="self" href="https://example.org/feed.xml"/>
          <link rel="alternate" href="https://example.org/booking"/>
          <summary>Helps freelancers manage bookings</summary><published>2026-01-01T00:00:00Z</published>
          </entry></feed>'''
        with patch.object(digest, "fetch", return_value=xml):
            item = digest.src_producthunt()[0]
        self.assertEqual(item["url"], "https://example.org/booking")
        self.assertGreater(item["age_h"], 72)

    def test_posting_gate_and_chunking(self):
        useful = record("How founders find paying customers", extra="x" * 500,
                        startup_reason="고객 인터뷰에서 지불 의사 확인")
        items = [{**useful, "url": f"https://example.org/{n}"} for n in range(20)]
        blocks = digest.to_blocks([record("Startup raises $50M"), *items], "창업정보")
        joined = "".join(blocks)
        self.assertGreater(len(blocks), 1)
        self.assertTrue(all(len(block) <= 1900 for block in blocks))
        self.assertNotIn("raises $50M", joined)
        self.assertIn("고객 인터뷰에서 지불 의사 확인", joined)
        for item in items:
            self.assertIn(item["url"], joined)
        original = "long" * 2000 + "\n끝"
        self.assertEqual("".join(chunk_text(original)), original)

    def test_llm_adds_only_provided_fields(self):
        item = record("Founder's customer discovery guide")
        response = json.dumps([{"i": 0, "startup_relevant": True,
                               "ko": "고객 인터뷰 가이드", "startup_reason": "고객 검증 질문 참고",
                               "customer": "소상공인", "problem": "", "business_model": "", "kw": ["고객 인터뷰"]}])
        with patch.object(llm_enrich, "_call", return_value=response):
            enriched = llm_enrich.translate_batch([item])[0]
        self.assertEqual(enriched["customer"], "소상공인")
        self.assertNotIn("business_model", enriched)
        self.assertEqual(enriched["startup_reason"], "고객 검증 질문 참고")

    def test_llm_failure_keeps_rule_filter(self):
        item = record("Founder guide to pricing")
        with patch.object(llm_enrich, "_call", side_effect=RuntimeError("fixture failure")), \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(llm_enrich.translate_batch([item]), [item])
        self.assertEqual(len(select_startup_items([item])), 1)

    def test_llm_empty_evidence_is_inconclusive(self):
        item = record("Booking Buddy", evidence=[])
        with patch.object(llm_enrich, "_call", return_value='[{"i":0,"v":"없음","why":"없다"}]'):
            llm_enrich.judge_batch([item])
        self.assertEqual(item["verdict"], "불명")


if __name__ == "__main__":
    unittest.main()
