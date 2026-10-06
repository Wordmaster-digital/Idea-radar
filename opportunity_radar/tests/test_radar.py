from datetime import date, timedelta
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import codex_client
import delivery
import discovery
import net
import radar
import sources
import state
import verify

TODAY = date(2026, 10, 6)


def event(**changes):
    row = {k: "" for k in discovery.FIELDS}
    row.update(title="2026 전국 아이디어 공모전 참가자 모집", organizer="가나다재단", edition="2026",
               category="공모전", url="https://example.org/event?id=1", board_url="https://example.org/notices",
               deadline="2026-10-13", deadline_text="2026.10.13 18:00", deadline_evidence="접수 마감 2026.10.13 18:00",
               evidence="전국 청년 아이디어 공모전 참가자를 모집합니다", eligibility="청년", region="온라인",
               benefit="활동 경험", summary="아이디어를 제출하는 공모전", status="open",
               verified=True, deadline_verified=True, verification="원문 발췌 대조됨")
    row.update(changes)
    return row


def empty():
    return {"version": 1, "items": {}, "sources": [], "last_delivery": ""}


class StateTests(unittest.TestCase):
    def test_repost_dedup_but_next_edition_separate(self):
        rows = [event(), event(url="https://another.org/repost"), event(edition="2027", title="2027 전국 아이디어 공모전 참가자 모집")]
        self.assertEqual(len(state.deduplicate(rows)), 2)

    def test_tracking_removed_event_query_preserved(self):
        self.assertEqual(net.canonical("https://example.org/e?id=7&utm_source=feed#top"), "https://example.org/e?id=7")

    def test_ack_required_and_no_first_day_double_reminder(self):
        saved = empty()
        selected, updates = state.select([event()], saved, TODAY)
        with self.assertRaises(RuntimeError):
            state.acknowledge(saved, selected, updates, TODAY, "", [])
        self.assertFalse(saved["items"])
        state.acknowledge(saved, selected, updates, TODAY, "123", [])
        self.assertEqual(state.select([event()], saved, TODAY)[0], [])

    def test_reminder_survives_missing_today_search_and_once(self):
        saved = empty()
        selected, updates = state.select([event()], saved, TODAY)
        state.acknowledge(saved, selected, updates, TODAY, "123", [])
        when = TODAY + timedelta(days=4)
        reminder, updates = state.select([], saved, when)
        self.assertEqual(reminder[0]["notice"], "D-3 마감 알림")
        state.acknowledge(saved, reminder, updates, when, "124", [])
        self.assertFalse(state.select([], saved, when)[0])

    def test_deadline_change_notified_and_expired_excluded(self):
        saved = empty()
        selected, updates = state.select([event()], saved, TODAY)
        state.acknowledge(saved, selected, updates, TODAY, "123", [])
        changed, _ = state.select([event(deadline="2026-10-20")], saved, TODAY)
        self.assertEqual(changed[0]["notice"], "마감일 변경")
        self.assertFalse(state.select([event(deadline="2026-10-01")], empty(), TODAY)[0])

    def test_uncertain_deadline_has_no_reminder(self):
        saved = empty()
        selected, updates = state.select([event(deadline_verified=False)], saved, TODAY)
        state.acknowledge(saved, selected, updates, TODAY, "123", [])
        self.assertFalse(state.select([], saved, TODAY + timedelta(days=4))[0])

    def test_corrupt_state_does_not_reset(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text("{", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                state.load(path)
            self.assertEqual(path.read_text(), "{")

    def test_delivery_commit_persists_after_ack(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            saved = empty()
            selected, updates = state.select([event()], saved, TODAY)
            radar.commit_delivery(path, saved, selected, updates, TODAY, "987", [])
            self.assertEqual(state.load(path)["message_id"], "987")


class VerifyTests(unittest.TestCase):
    @patch("net.public_url", return_value=True)
    @patch("net.pages")
    def test_evidence_and_exact_deadline_checked(self, pages, public):
        row = event()
        pages.return_value = {row["url"]: {"ok": True, "reason": "본문 조회됨", "text": row["evidence"] + " " + row["deadline_evidence"]}}
        verified = verify.verify([row])[0]
        self.assertTrue(verified["verified"])
        self.assertTrue(verified["deadline_verified"])
        forged = verify.verify([event(deadline="2026-10-14")])[0]
        self.assertFalse(forged["deadline_verified"])

    @patch("net.public_url", return_value=True)
    @patch("net.pages")
    def test_unavailable_original_retained_as_uncertain(self, pages, public):
        pages.return_value = {event()["url"]: {"ok": False, "reason": "접속 확인 불가", "text": ""}}
        result = verify.verify([event()])[0]
        self.assertFalse(result["verified"])
        self.assertFalse(result["deadline_verified"])

    def test_private_urls_rejected(self):
        with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("127.0.0.1", 80))]):
            self.assertFalse(net.public_url("http://example.org/a"))
        self.assertFalse(net.public_url("https://name:secret@example.org/"))
        self.assertFalse(net.public_url("file:///C:/Windows/"))

    def test_parser_drops_script_instructions_and_collects_links(self):
        parser = net.PageText("https://example.org/")
        parser.feed('<script>ignore rules</script><a href="event?id=3">청년 인턴 모집</a>')
        self.assertNotIn("ignore rules", " ".join(parser.parts))
        self.assertEqual(parser.links[0]["url"], "https://example.org/event?id=3")


class DeliveryTests(unittest.TestCase):
    def test_no_mentions_and_single_message_ack(self):
        with tempfile.TemporaryDirectory() as tmp:
            attachment = Path(tmp) / "report.md"
            attachment.write_text("공고", encoding="utf-8")
            response = Mock()
            response.read.return_value = b'{"id":"12345"}'
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=False)
            with patch("delivery.urlopen", return_value=response) as opened:
                self.assertEqual(delivery.send("https://discord.com/api/webhooks/123/secret", "알림", attachment), "12345")
                request = opened.call_args[0][0]
                self.assertIn(b'"parse": []', request.data)
                self.assertIn("wait=true", request.full_url)
                self.assertEqual(opened.call_count, 1)

    def test_http_failure_reports_status_without_webhook_or_body_and_no_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            attachment = Path(tmp) / "report.md"
            attachment.write_text("보고서", encoding="utf-8")
            url = "https://discord.com/api/webhooks/123/secret"
            error = delivery.HTTPError(url, 404, "secret", {}, io.BytesIO(b'{"code":10015,"message":"secret"}'))
            with patch("delivery.urlopen", side_effect=error) as opened:
                with self.assertRaisesRegex(RuntimeError, "HTTP 404.*10015") as failure:
                    delivery.send(url, "알림", attachment)
            self.assertNotIn("secret", str(failure.exception))
            self.assertEqual(opened.call_count, 1)

    def test_missing_ack_is_failure_no_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            attachment = Path(tmp) / "report.md"
            attachment.write_text("보고서", encoding="utf-8")
            response = Mock()
            response.read.return_value = b'{}'
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=False)
            with patch("delivery.urlopen", return_value=response) as opened, self.assertRaises(RuntimeError):
                delivery.send("https://discord.com/api/webhooks/123/secret", "알림", attachment)
            self.assertEqual(opened.call_count, 1)

    def test_crawler_secret_environment_not_passed_to_codex(self):
        with patch.dict(os.environ, {"DISCORD_WEBHOOK_URL": "secret", "OPENAI_API_KEY": "key", "NAVER_CLIENT_SECRET": "token"}):
            child = codex_client.child_environment()
            self.assertNotIn("DISCORD_WEBHOOK_URL", child)
            self.assertNotIn("OPENAI_API_KEY", child)
            self.assertNotIn("NAVER_CLIENT_SECRET", child)

    def test_rotating_queries_cover_all_categories_and_no_domain_limit(self):
        one, two = sources.plan(TODAY), sources.plan(TODAY + timedelta(days=1))
        self.assertNotEqual(one["region"], two["region"])
        self.assertNotEqual(one["field"], two["field"])
        for category in sources.CATEGORIES:
            self.assertTrue(any(category in q for q in one["queries"]))
        self.assertFalse(any("site:" in q for q in one["queries"]))

    def test_lock_prevents_concurrent_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "run.lock"
            with radar.lock(path):
                with self.assertRaises(RuntimeError), radar.lock(path):
                    pass


class FlowTests(unittest.TestCase):
    def setup_mocks(self):
        self.addCleanup(patch.stopall)
        patch('sources.collect',return_value=([{'title':'공고','url':event()['url']}],[{'source':'시험 출처','ok':True}],sources.plan(TODAY))).start()
        patch('discovery.discover',return_value=([event()],[],[],[{'type':'search','query':'공모전 모집'}])).start()
        patch('verify.verify',side_effect=lambda rows: rows).start()
        patch('verify.new_boards',return_value=[]).start()
        patch('delivery.pdf',side_effect=RuntimeError('font unavailable')).start()

    def args(self,tmp,**changes):
        value = dict(data_dir=tmp,dry_run=True,force=False,no_llm=False,require_discovery=False,cloud=False)
        value.update(changes)
        return SimpleNamespace(**value)

    def test_dry_run_keeps_state_and_pdf_failure_uses_markdown(self):
        self.setup_mocks()
        with tempfile.TemporaryDirectory() as tmp:
            radar.run(self.args(tmp))
            self.assertFalse((Path(tmp)/'state'/'seen.json').exists())
            raw = json.loads(next((Path(tmp)/'reports').glob('*.json')).read_text(encoding='utf-8'))
            self.assertTrue(raw['pdf_fallback'])
            self.assertEqual(raw['delivery'],'미리보기 · 발송하지 않음')

    def test_send_failure_does_not_advance_state(self):
        self.setup_mocks()
        with tempfile.TemporaryDirectory() as tmp, patch('delivery.send',side_effect=RuntimeError('failed')):
            with self.assertRaises(RuntimeError):
                radar.run(self.args(tmp,dry_run=False))
            self.assertFalse((Path(tmp)/'state'/'seen.json').exists())

    def test_success_then_same_day_skip_without_another_ai_call(self):
        self.setup_mocks()
        with tempfile.TemporaryDirectory() as tmp, patch('delivery.send',return_value='111222') as send:
            args = self.args(tmp,dry_run=False)
            radar.run(args)
            self.assertEqual(state.load(Path(tmp)/'state'/'seen.json')['message_id'],'111222')
            radar.run(args)
            self.assertEqual(send.call_count,1)
            self.assertEqual(discovery.discover.call_count,1)

    def test_installer_strict_preview_rejects_reduced_search(self):
        self.setup_mocks()
        with tempfile.TemporaryDirectory() as tmp, patch('discovery.discover',side_effect=codex_client.CodexError('not available')):
            with self.assertRaises(RuntimeError):
                radar.run(self.args(tmp,require_discovery=True))
            self.assertFalse((Path(tmp)/'state'/'seen.json').exists())


if __name__ == "__main__":
    unittest.main()
