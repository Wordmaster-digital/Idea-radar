"""Bounded recent news research without API keys or live network requests."""
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import unittest
from urllib.parse import parse_qs, urlsplit
from xml.sax.saxutils import escape
from unittest.mock import patch, MagicMock

import market_evidence

TODAY = "2026-09-19"
NOW = datetime(2026, 9, 19, 0, tzinfo=timezone.utc)


def item(title, url, days=0, stamp=None):
    stamp = stamp or format_datetime(NOW - timedelta(days=days))
    return (f"<item><title>{escape(title)} - 가상일보</title><link>{escape(url)}</link>"
            f"<pubDate>{stamp}</pubDate><source>가상일보</source></item>")


def feed(*items):
    return "<rss><channel>" + "".join(items) + "</channel></rss>"


class MarketEvidenceTests(unittest.TestCase):
    def test_http_response_bytes_are_decoded_before_shared_xml_parser(self):
        response = MagicMock()
        response.read.return_value = feed(item("수요 단서", "https://example.com/1")).encode("utf-8")
        response.__enter__.return_value = response
        with patch("market_evidence.urllib.request.urlopen", return_value=response):
            records, warnings = market_evidence.collect(TODAY)
        self.assertEqual(warnings, [])
        self.assertEqual(records[0]["title"], "수요 단서")
        response.read.assert_called_with(2_000_000)

    def test_deduplicates_articles_and_preserves_source_date_link_and_query(self):
        calls = []
        def fetch(url):
            calls.append(url)
            return feed(item("실제 확인이 필요한 합성 제목", "https://example.com/1"))
        records, warnings = market_evidence.collect(TODAY, fetcher=fetch)
        self.assertEqual(warnings, [])
        self.assertEqual(len(calls), 3)
        self.assertEqual(len(records), 1)
        row = records[0]
        self.assertEqual(row["title"], "실제 확인이 필요한 합성 제목")
        self.assertEqual(row["date"], TODAY)
        self.assertEqual(row["outlet"], "가상일보")
        self.assertIn("제목만", row["scope"])
        self.assertTrue(row["query"])
        self.assertTrue(all("when:90d" in parse_qs(urlsplit(u).query)["q"][0] for u in calls))

    def test_rejects_old_future_invalid_dates_and_non_web_links(self):
        xml = feed(item("적합", "https://example.com/valid", 89),
                   item("오래됨", "https://example.com/old", 90),
                   item("미래", "https://example.com/future", -1),
                   item("날짜 없음", "https://example.com/bad", stamp="not-a-date"),
                   item("로컬", "file:///secret"), item("잘못된 주소", "https:///no-host"),
                   item("파싱 불가 주소", "https://[invalid"))
        records, _ = market_evidence.collect(TODAY, fetcher=lambda url: xml)
        self.assertEqual([r["title"] for r in records], ["적합"])

    def test_keeps_latest_four_per_query(self):
        xml = feed(*(item(f"제목{i}", f"https://example.com/{i}", i) for i in reversed(range(10))))
        records, _ = market_evidence.collect(TODAY, fetcher=lambda url: xml)
        self.assertEqual([r["title"] for r in records], [f"제목{i}" for i in range(4)])

    def test_targeted_search_is_limited_to_three_parents_and_two_queries_each(self):
        calls = []
        def fetch(url):
            calls.append(url)
            return feed()
        cards = [{"name": f"소재{i}", "kw": [f"기능{i}"]} for i in range(10)]
        records, warnings = market_evidence.collect(TODAY, cards, fetch)
        self.assertEqual(len(calls), 6)
        self.assertEqual(records, [])
        self.assertTrue(warnings)

    def test_failed_and_malformed_feeds_are_safe_and_explicit(self):
        def fetch(url):
            if "디지털" in parse_qs(urlsplit(url).query)["q"][0]:
                return "not xml"
            raise OSError("private-secret")
        records, warnings = market_evidence.collect(TODAY, fetcher=fetch)
        self.assertEqual(records, [])
        self.assertEqual(len(warnings), 4)
        self.assertNotIn("private-secret", " ".join(warnings))


if __name__ == "__main__":
    unittest.main()
