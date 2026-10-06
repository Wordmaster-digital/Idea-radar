from datetime import date, timedelta
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import cloud_discovery as cloud
import delivery
import radar
import ranking
import sources
import state
from test_radar import event, empty

TODAY = date(2026,10,6)


def opportunity(index=1,**changes):
    row = event(title='글로벌 마케팅 무역 인턴 모집', organizer='KOTRA',
                context='해외시장 조사와 영어 바이어 상담자료 작성', eligibility='공고 확인 필요',
                region='세종', category='인턴', url='https://example.org/jobs/'+str(index))
    row.update(changes)
    return row


class RankingTests(unittest.TestCase):
    def setUp(self):
        self.profile = ranking.load_profile()

    def brief(self,rows,saved=None):
        saved = saved or empty()
        selected, updates = state.select(rows,saved,TODAY)
        return ranking.briefing(saved,updates,selected,self.profile,TODAY)

    def test_career_and_english_match_outrank_generic_contest(self):
        result = self.brief([opportunity(),opportunity(2,title='전국 시 문학 공모전',context='시 작품 제출',organizer='문화재단',category='공모전')])
        self.assertEqual(result['top10'][0]['organizer'],'KOTRA')
        self.assertEqual(result['top10'][0]['fit_breakdown']['language'],15)
        self.assertEqual(len(result['top10']),1)
        self.assertEqual(len(result['remaining']),1)

    def test_offline_far_region_retained_in_groups_but_not_top(self):
        result = self.brief([opportunity(region='제주 근무')])
        self.assertFalse(result['top10'])
        self.assertEqual(len(result['remaining']),1)
        self.assertIn('선호 지역 밖',result['remaining'][0]['region_match'])

    def test_global_does_not_invent_english_use_or_qualified_status(self):
        row=ranking.evaluate(opportunity(context='마케팅 업무',eligibility='TOEIC 900점 이상'),self.profile,TODAY)
        self.assertEqual(row['fit_breakdown']['language'],0)
        self.assertTrue(any('어학 점수 대조' in v for v in row['fit_warnings']))
        self.assertNotIn('eligible',row)

    def test_unverified_source_and_senior_requirement_do_not_make_top(self):
        result = self.brief([opportunity(verified=False),opportunity(2,title='무역 마케팅 경력 5년 이상 담당자 채용',context='영어',category='채용')])
        self.assertFalse(result['top10'])
        self.assertEqual(len(result['remaining']),2)

    def test_saved_open_notice_is_ranked_on_a_day_without_new_notices(self):
        saved=empty()
        selected,updates=state.select([opportunity()],saved,TODAY)
        with tempfile.TemporaryDirectory() as tmp:
            radar.commit_delivery(Path(tmp)/'state.json',saved,selected,updates,TODAY,'123',[])
        result=ranking.briefing(saved,{},[],self.profile,TODAY+timedelta(days=1))
        self.assertEqual(len(result['top10']),1)
        self.assertEqual(result['top10'][0]['notice'],'누적 공고')

    def test_expired_and_explicitly_closed_saved_notices_leave_current_pool(self):
        rows=[opportunity(status='closed'),opportunity(2,deadline='2026-10-01')]
        result=self.brief(rows)
        self.assertEqual(result['pool_count'],0)

    def test_exactly_ten_unique_recommendations_and_no_duplication_in_remaining(self):
        rows=[opportunity(i,title=f'글로벌 마케팅 무역 {i}번 인턴 모집',organizer=f'기관 {i}') for i in range(1,16)]
        result=self.brief(rows)
        self.assertEqual([r['rank'] for r in result['top10']],list(range(1,11)))
        self.assertEqual(len(result['remaining']),5)
        self.assertFalse({r['id'] for r in result['top10']} & {r['id'] for r in result['remaining']})
        self.assertEqual(sum(len(g['items']) for g in result['by_field']),5)
        self.assertEqual(sum(len(g['items']) for g in result['by_institution']),5)

    def test_rank_change_and_institution_aliases(self):
        result=self.brief([opportunity()])
        saved=empty()
        saved['recommendations']=[{'id':result['top10'][0]['id'],'rank':3,'score':0}]
        updated=self.brief([opportunity()],saved)
        self.assertEqual(updated['top10'][0]['rank_change'],'순위 상승 2')
        self.assertEqual(ranking.organization(opportunity(organizer='대한무역투자진흥공사')),'KOTRA(대한무역투자진흥공사)')

    def test_top_ten_embeds_and_grouped_report_fit_discord_limits(self):
        result=self.brief([opportunity(i,title=f'무역 마케팅 {i}번 영어 인턴 모집 '+('긴 이름 '*40),organizer=f'기관 {i}') for i in range(12)])
        report={'date':TODAY.isoformat(),'briefing':result,'selected':[],'coverage':[],'degraded':'','cloud_stats':{}}
        cards=delivery.embeds(report)
        self.assertEqual(len(cards[0]['fields']),10)
        self.assertLess(len(delivery.summary(report)),2000)
        self.assertTrue(all(len(f['name'])<=256 and len(f['value'])<=1024 for f in cards[0]['fields']))
        text=delivery.markdown(report)
        for title in ('먼저 검토할 TOP 10','그 외 후보 · 분야별','주관기관별 색인','기회 유형별 색인'):
            self.assertIn(title,text)
        with tempfile.TemporaryDirectory() as tmp:
            file=Path(tmp)/'report.md'
            file.write_text(text,encoding='utf-8')
            response=Mock()
            response.__enter__=Mock(return_value=response)
            response.__exit__=Mock(return_value=False)
            response.read.return_value=b'{"id":"123"}'
            with patch('delivery.urlopen',return_value=response) as opened:
                self.assertEqual(delivery.send('https://discord.com/api/webhooks/123/secret',delivery.summary(report),file,embeds=cards),'123')
                self.assertIn(b'"embeds":',opened.call_args[0][0].data)

    def test_invalid_private_profile_is_redacted(self):
        with patch.dict('os.environ',{'OPPORTUNITY_PROFILE_JSON':'{"private":"sensitive-resume"}'}):
            with self.assertRaises(RuntimeError) as caught:
                ranking.load_profile()
        self.assertNotIn('sensitive-resume',str(caught.exception))

    def test_focused_queries_keep_broad_discovery(self):
        queries=cloud.queries(sources.plan(TODAY),self.profile)
        self.assertTrue(any('KOTRA' in q for q in queries))
        self.assertTrue(any('세종' in q for q in queries))
        for word in ('해커톤','공모전','대외활동','인턴','일경험','신입'):
            self.assertTrue(any(word in q for q in queries))
        self.assertFalse(any('site:' in q for q in queries))

    def test_notice_fields_use_content_not_navigation_and_short_region(self):
        title='글로벌 마케팅 인턴 모집'
        page={'ok':True,'text':'메뉴 영어 관련공고 '+title+' 주최: KOTRA 근무지: 세종 지원자격: 공고 참고 접수 마감 2026.10.13 관련 공고 영어 경력 8년 이상', 'links':[],'structured':[]}
        row=cloud.extract({'title':title,'url':'https://example.org/job/1'},page)
        self.assertEqual(row['organizer'],'KOTRA')
        self.assertEqual(row['region'],'세종')
        self.assertNotIn('경력 8년',row['context'])
        self.assertNotIn('메뉴 영어',row['context'])
        self.assertEqual(ranking.evaluate(row,self.profile,TODAY)['fit_breakdown']['language'],0)

    def test_rolling_hiring_does_not_use_portal_expiry_as_application_deadline(self):
        title='영어 해외영업 인턴 채용'
        page={'ok':True,'text':title+' 주최: 기업 근무지: 서울 접수기간 시작일 2026.10.06 마감일 채용 시 마감 상세내용 영어 해외영업 담당', 'links':[], 'structured':[{'@type':'JobPosting','title':title,'validThrough':'2027-01-04','hiringOrganization':{'name':'기업'}}]}
        row=cloud.extract({'title':title,'url':'https://example.org/jobs/1'},page)
        self.assertEqual(row['deadline'],'')
        self.assertFalse(row['deadline_verified'])
        self.assertIn('채용 시 마감',row['deadline_text'])

    def test_closed_update_preserved_and_does_not_trigger_old_reminder(self):
        saved=empty()
        selected,updates=state.select([opportunity()],saved,TODAY)
        state.acknowledge(saved,selected,updates,TODAY,'123',[])
        selected,updates=state.select([opportunity(status='closed')],saved,TODAY)
        self.assertFalse(selected)
        self.assertEqual(ranking.briefing(saved,updates,selected,self.profile,TODAY)['pool_count'],0)

    def test_head_title_is_not_visible_notice_evidence_but_json_metadata_remains(self):
        parser=cloud.net.PageText('https://example.org/jobs/1')
        parser.feed('<head><title>영어 인턴 모집</title><script type="application/ld+json">{"@type":"JobPosting","title":"영어 인턴 모집"}</script></head><body>로그인 후 확인하세요</body>')
        self.assertNotIn('영어 인턴 모집',' '.join(parser.parts))
        self.assertEqual(len(parser.structured),1)

    def test_format_upgrade_sends_once_and_ranking_history_waits_for_ack(self):
        from types import SimpleNamespace
        saved=empty()
        saved['last_delivery']=TODAY.isoformat()
        saved['digest_version']=1
        report=self.brief([opportunity()])
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'state.json'
            with self.assertRaises(RuntimeError):
                radar.commit_delivery(path,saved,[],{},TODAY,'',[],report)
            self.assertEqual(saved['digest_version'],1)
            self.assertNotIn('recommendations',saved)
            radar.commit_delivery(path,saved,[],{},TODAY,'123',[],report)
            self.assertEqual(state.load(path)['digest_version'],2)
            self.assertEqual(state.load(path)['recommendations'][0]['rank'],1)


if __name__=='__main__':
    unittest.main()
