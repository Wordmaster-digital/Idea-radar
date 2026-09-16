"""kr_sources 테스트 — 합성 피드로 파싱·기간·실패 처리를 확인한다."""
import contextlib
import io
import unittest

import fixtures
import kr_sources

NOW = fixtures.NOW


def fetcher_for(mapping):
    """주소에 포함된 조각으로 합성 응답을 고르는 가짜 fetch."""
    calls = []

    def fetch(url):
        calls.append(url)
        for key, body in mapping.items():
            if key in url:
                return body
        raise AssertionError(f"예상하지 못한 주소: {url}")

    return fetch, calls


def boom(url):
    raise OSError("조회 실패")


class GnewsTests(unittest.TestCase):
    def test_parses_recent_item_only(self):
        fetch, calls = fetcher_for({"news.google.com": fixtures.GNEWS_XML})
        items = kr_sources.gnews(NOW, 24, fetch, queries=["앱 등장"])
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "동네 빨래방 예약 앱 '빨래톡' 등장")
        self.assertEqual(items[0]["outlet"], "가상일보")
        self.assertEqual(items[0]["source"], "gnews")
        self.assertIn("when%3A1d", calls[0])
        self.assertIn("hl=ko", calls[0])

    def test_window_scales_to_days(self):
        fetch, calls = fetcher_for({"news.google.com": fixtures.GNEWS_XML})
        kr_sources.gnews(NOW, 48, fetch, queries=["앱 등장"])
        self.assertIn("when%3A2d", calls[0])

    def test_failure_is_recorded(self):
        errors = []
        with contextlib.redirect_stderr(io.StringIO()):
            items = kr_sources.gnews(NOW, 24, boom, errors, queries=["앱 등장"])
        self.assertEqual(items, [])
        self.assertEqual(len(errors), 1)


class MediaTests(unittest.TestCase):
    def test_recent_item_with_trimmed_description(self):
        fetch, _ = fetcher_for({"media": fixtures.MEDIA_XML})
        items = kr_sources.media(NOW, 24, fetch,
                                 feeds=[("가상매체", "https://media.example/feed")])
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["outlet"], "가상매체")
        self.assertEqual(len(items[0]["desc"]), 300)
        self.assertNotIn("<p>", items[0]["desc"])


class AppstoreTests(unittest.TestCase):
    def test_keeps_only_recent_release(self):
        fetch, _ = fetcher_for({"itunes.apple.com": fixtures.APPSTORE_JSON})
        items = kr_sources.appstore(NOW, fetch)
        self.assertEqual([i["title"] for i in items], ["빨래톡"])
        self.assertEqual(items[0]["chart_rank"], 1)
        self.assertEqual(items[0]["app_id"], "111")
        self.assertEqual(items[0]["outlet"], "앱스토어 무료 #1")


class CollectTests(unittest.TestCase):
    def test_all_sources_failing_reports_every_source(self):
        with contextlib.redirect_stderr(io.StringIO()):
            items, errors = kr_sources.collect(NOW, 24, boom)
        self.assertEqual(items, [])
        self.assertEqual(len(errors), kr_sources.SOURCE_COUNT)


if __name__ == "__main__":
    unittest.main()
