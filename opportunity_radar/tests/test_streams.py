from datetime import date, timedelta
import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import delivery
import radar
import ranking
import state
import streams
from test_radar import empty, event

TODAY = date(2026, 10, 7)


def rows():
    return [event(title=f'2026 글로벌 마케팅 영어 {kind} 모집', category=kind,
                  url=f'https://example.org/{index}', organizer=f'기관 {index}',
                  context='해외시장 조사와 마케팅 기획', region='서울')
            for index, kind in enumerate(('공모전', '대외활동', '해커톤', '인턴', '일경험', '채용'))]


def common(candidates):
    return {'date': TODAY.isoformat(), 'all_discovered': candidates, 'coverage': [],
            'plan': {'region': '세종', 'field': '마케팅'}, 'new_sources': [],
            'searches': [], 'cloud_stats': {}, 'degraded': '', 'delivery': '발송 대기'}


class SplitDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.profile = ranking.load_profile()

    def parts(self, saved=None, candidates=None, day=TODAY):
        saved = saved if saved is not None else empty()
        candidates = rows() if candidates is None else candidates
        selected, updates = state.select(candidates, saved, day)
        return streams.reports(common(candidates), saved, updates, selected, self.profile, day)

    def test_all_six_types_route_once_and_only_work_receives_ranking(self):
        activity, career = self.parts()
        self.assertEqual({r['category'] for r in activity[1]['selected']}, streams.GROUPS['activities'])
        self.assertFalse(delivery.embeds(activity[1]))
        self.assertEqual({r['category'] for r in career[1]['briefing']['top10']}, streams.GROUPS['career'])
        self.assertEqual(len(career[1]['briefing']['top10']), 3)
        self.assertNotIn('TOP 10', delivery.markdown(activity[1]))
        self.assertIn('공모전·대외활동', delivery.summary(activity[1]))
        self.assertIn('인턴·일경험·채용', delivery.summary(career[1]))
        self.assertLess(len(delivery.summary(activity[1])), 2000)
        self.assertFalse({r['id'] for r in activity[1]['selected']} & {r['id'] for r in career[1]['briefing']['top10']})

    def test_upgrade_introduces_seen_activities_and_preserves_work_history(self):
        saved = empty()
        selected, updates = state.select(rows(), saved, TODAY)
        state.acknowledge(saved, selected, updates, TODAY, '123', [])
        saved['digest_version'] = 2
        saved['recommendations'] = [{'id': r['id'], 'rank': n+1} for n, r in enumerate(selected)]
        activity, career = self.parts(saved)
        self.assertFalse(streams.complete(saved, TODAY))
        self.assertEqual(len(activity[1]['selected']), 3)
        self.assertTrue(all(r['notice'] == '누적 공고·형식 전환' for r in activity[1]['selected']))
        self.assertEqual(career[1]['briefing']['pool_count'], 3)
        self.assertTrue(all(r['notice'] == '누적 공고' for r in career[1]['briefing']['top10']))

    def test_next_day_activity_keeps_original_new_notice_behavior(self):
        saved = empty()
        with tempfile.TemporaryDirectory() as tmp:
            for group, report, updates in self.parts(saved):
                streams.acknowledge(Path(tmp)/'seen.json', saved, report, updates, TODAY, '123' if group == 'activities' else '456', [])
            activity, career = self.parts(saved, day=TODAY+timedelta(days=1))
            self.assertFalse(activity[1]['selected'])
            self.assertNotIn('format_transition', activity[1])
            self.assertEqual(len(career[1]['briefing']['top10']), 3)
            self.assertFalse(streams.complete(saved, TODAY+timedelta(days=1)))

    def test_no_ack_does_not_advance_either_group(self):
        saved = empty()
        group, report, updates = self.parts(saved)[0]
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError):
                streams.acknowledge(Path(tmp)/'seen.json', saved, report, updates, TODAY, '', [])
            self.assertFalse(saved['items'])
            self.assertNotIn('deliveries', saved)

    def test_partial_failure_resumes_only_failed_group_and_keeps_receipts(self):
        with tempfile.TemporaryDirectory() as tmp, patch('delivery.pdf', side_effect=RuntimeError('font')):
            saved = empty()
            path = Path(tmp)/'state'/'seen.json'
            selected, updates = state.select(rows(), saved, TODAY)
            args = SimpleNamespace(dry_run=False, force=False)
            with patch('delivery.send', side_effect=['123', RuntimeError('career failed')]) as sent:
                with self.assertRaisesRegex(RuntimeError, 'career failed'):
                    radar.send_groups(args, Path(tmp), path, saved, common(rows()), updates, selected, self.profile, TODAY, [])
                self.assertEqual(sent.call_count, 2)
            recovered = state.load(path)
            self.assertTrue(streams.delivered(recovered, 'activities', TODAY))
            self.assertFalse(streams.complete(recovered, TODAY))
            self.assertEqual(recovered['last_delivery'], '')
            self.assertEqual({r['data']['category'] for r in recovered['items'].values()}, streams.GROUPS['activities'])
            selected, updates = state.select(rows(), recovered, TODAY)
            with patch('delivery.send', return_value='456') as sent:
                radar.send_groups(args, Path(tmp), path, recovered, common(rows()), updates, selected, self.profile, TODAY, [])
                self.assertEqual(sent.call_count, 1)
                self.assertIn('인턴·일경험·채용', sent.call_args.args[1])
            recovered = state.load(path)
            self.assertTrue(streams.complete(recovered, TODAY))
            self.assertEqual(recovered['deliveries']['activities']['message_id'], '123')
            self.assertEqual(recovered['deliveries']['career']['message_id'], '456')
            self.assertEqual(recovered['digest_version'], streams.VERSION)
            self.assertEqual(len(recovered['recommendations']), 3)

    def test_activity_failure_does_not_block_career_acknowledgement(self):
        with tempfile.TemporaryDirectory() as tmp, patch('delivery.pdf', side_effect=RuntimeError('font')):
            saved = empty()
            path = Path(tmp)/'seen.json'
            selected, updates = state.select(rows(), saved, TODAY)
            with patch('delivery.send', side_effect=[RuntimeError('activity failed'), '456']):
                with self.assertRaisesRegex(RuntimeError, 'activity failed'):
                    radar.send_groups(SimpleNamespace(dry_run=False, force=False), Path(tmp), path,
                                      saved, common(rows()), updates, selected, self.profile, TODAY, [])
            recovered = state.load(path)
            self.assertTrue(streams.delivered(recovered, 'career', TODAY))
            self.assertFalse(streams.delivered(recovered, 'activities', TODAY))

    def test_reports_have_separate_names_and_no_cross_type_content(self):
        with tempfile.TemporaryDirectory() as tmp, patch('delivery.pdf', side_effect=RuntimeError('font')):
            files = [delivery.write_report(Path(tmp), report)[0] for _, report, _ in self.parts()]
            self.assertNotEqual(files[0], files[1])
            for index, path in enumerate(files):
                report = json.loads(path.read_text(encoding='utf-8'))
                self.assertEqual(report['delivery_group'], 'activities' if index == 0 else 'career')
                self.assertEqual(len(report['collection']), 6)
                self.assertEqual(len(report['all_discovered']), 3)

    def test_closed_activities_are_excluded_from_upgrade_listing(self):
        parts = self.parts(candidates=[event(status='closed'), event(url='https://example.org/expired', deadline='2026-10-01')])
        self.assertFalse(parts[0][1]['selected'])
        self.assertEqual(parts[0][1]['pool_count'], 0)

    def test_corrupt_group_receipt_cannot_silently_reset_or_resend(self):
        saved = {**empty(), 'deliveries': {'activities': {'date': TODAY.isoformat(), 'version': 3, 'message_id': ''}}}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'seen.json'
            path.write_text(json.dumps(saved), encoding='utf-8')
            with self.assertRaises(RuntimeError):
                state.load(path)


if __name__ == '__main__':
    unittest.main()
