import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from email.parser import BytesParser
from email.policy import default
from urllib.parse import parse_qs, urlsplit

import development_report
import fixtures
import idea_development
import kr_digest
import link_check
import pdf_report
import report_delivery


class LinkTests(unittest.TestCase):
    def test_duplicates_are_checked_once_and_failures_never_become_clickable(self):
        calls = []
        def probe(url):
            calls.append(url)
            if "bad" in url:
                raise OSError("sensitive detail")
            return True, "https://example.com/final", 200
        md = "[good](https://example.com/good) [again](https://example.com/good) [bad](https://example.com/bad)"
        result = link_check.check(link_check.links(md), probe)
        output = link_check.sanitize(md, result)
        self.assertEqual(len(calls), 2)
        self.assertEqual(output.count("https://example.com/final"), 2)
        self.assertNotIn("https://example.com/bad", output)
        self.assertIn("링크 확인 불가", output)
        self.assertNotIn("sensitive detail", str(result))

    def test_bare_addresses_and_nested_article_titles_are_checked(self):
        md = "[[인터뷰] 제목](https://example.com/one) https://example.com/two"
        self.assertEqual(link_check.links(md), ["https://example.com/one", "https://example.com/two"])
        out = link_check.sanitize(md, {})
        self.assertNotIn("https://", out)
        self.assertIn("인터뷰", out)

    def test_limit_marks_unchecked_links_instead_of_sending_them(self):
        urls = [f"https://example.com/{i}" for i in range(55)]
        calls = []
        def probe(url):
            calls.append(url)
            return True, url, 200
        result = link_check.check(urls, probe)
        self.assertEqual(len(calls), 50)
        self.assertFalse(result[urls[-1]]["ok"])

    def test_private_destinations_and_credentials_are_rejected(self):
        resolver = lambda *a, **kw: [(None, None, None, None, ("127.0.0.1", 80))]
        with self.assertRaises(ValueError):
            link_check.public_url("https://example.com", resolver)
        for url in ("file:///x", "https://user:secret@example.com", "http:///nohost"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                link_check.public_url(url, resolver)

    def test_redirects_revalidate_destination(self):
        from urllib.request import Request
        with patch.object(link_check, "public_url", side_effect=ValueError) as validate:
            with self.assertRaises(ValueError):
                link_check.PublicRedirect().redirect_request(Request("https://example.com"), None, 302, "", {}, "http://127.0.0.1")
        validate.assert_called_once_with("http://127.0.0.1")


class ReportTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "report.md"
        result = idea_development.run(fixtures.PipelineClient(), fixtures.merged_cards(), "2026-09-20",
                                     research_fn=fixtures.research, evidence_fn=lambda card: None)
        self.markdown = "\n".join(development_report.render(result, "2026-09-20", full=True))

    def test_brief_has_two_plans_metrics_and_usage_without_repeating_nine_alternatives(self):
        brief = pdf_report.summarize(self.markdown, usage=[{"input_tokens": 100, "output_tokens": 20}])
        self.assertLessEqual(len(brief), 1850)
        self.assertEqual(brief.count("• 핵심:"), 2)
        self.assertIn("통과 기준", brief)
        self.assertIn("분석 토큰 120", brief)
        self.assertEqual(len(pdf_report.plans(self.markdown)), 2)

    def test_pdf_receives_only_checked_links_but_original_is_saved_for_reuse(self):
        seen = []
        def writer(path, markdown, **kwargs):
            seen.append(markdown)
            Path(path).write_bytes(b"%PDF-test")
            return path
        bundle = report_delivery.prepare(self.markdown, self.path, checker=lambda urls: {}, writer=writer)
        self.assertNotIn("https://", seen[0])
        source = json.loads(self.path.with_suffix(".json").read_text(encoding="utf-8"))
        self.assertIn("https://", source["markdown"])
        self.assertEqual(bundle["attachment"].suffix, ".pdf")

    def test_pdf_failure_falls_back_to_a_readable_file_and_explicit_notice(self):
        def failed(*args, **kwargs):
            raise OSError("private detail")
        with contextlib.redirect_stderr(io.StringIO()):
            bundle = report_delivery.prepare(self.markdown, self.path, checker=lambda urls: {}, writer=failed)
        self.assertEqual(bundle["attachment"], self.path)
        self.assertIn("PDF 생성에 실패", bundle["summary"])
        self.assertTrue(self.path.is_file())

    def test_checker_failure_disables_links_and_does_not_call_a_model(self):
        def failed(*args):
            raise OSError("network")
        with contextlib.redirect_stderr(io.StringIO()):
            bundle = report_delivery.prepare(self.markdown, self.path, checker=failed, writer=failed)
        self.assertTrue(all(not value["ok"] for value in bundle["links"].values()))
        self.assertNotIn("https://", self.path.read_text(encoding="utf-8"))


class DiscordTests(unittest.TestCase):
    def test_multipart_pdf_is_one_message_with_wait_receipt_and_mentions_disabled(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.pdf"
            path.write_bytes(b"%PDF-test-data")
            captured = []
            @contextlib.contextmanager
            def opener(request, timeout):
                captured.append(request)
                yield SimpleNamespace(status=200, read=lambda: b'{"id":"999"}')
            receipt = kr_digest.send_discord("https://discord.example/hook?thread_id=1", "@everyone 요약", opener, path)
            self.assertEqual(receipt, "999")
            req = captured[0]
            self.assertEqual(parse_qs(urlsplit(req.full_url).query), {"thread_id": ["1"], "wait": ["true"]})
            mime = BytesParser(policy=default).parsebytes(("Content-Type: " + req.get_header("Content-type") + "\r\n\r\n").encode() + req.data)
            parts = list(mime.iter_parts())
            payload = json.loads(parts[0].get_payload(decode=True))
            self.assertEqual(payload["allowed_mentions"], {"parse": []})
            self.assertEqual(parts[1].get_filename(), "idea-radar.pdf")
            self.assertEqual(parts[1].get_payload(decode=True), path.read_bytes())

    def test_unconfirmed_response_is_not_delivery_success(self):
        for status, body in ((204, b""), (200, b"{}"), (200, b"not-json")):
            @contextlib.contextmanager
            def opener(request, timeout):
                yield SimpleNamespace(status=status, read=lambda: body)
            with self.subTest(status=status, body=body), self.assertRaises(ValueError):
                kr_digest.send_discord("https://discord.example/hook", "summary", opener)


if __name__ == "__main__":
    unittest.main()
