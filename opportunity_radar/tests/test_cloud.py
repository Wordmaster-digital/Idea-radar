from datetime import date
import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import cloud_discovery as cloud
import net
import radar
import sources
import state

TODAY = date(2026,10,6)
TITLE = '2026 청년 아이디어 공모전 참가자 모집'
URL = 'https://example.org/event?id=1'

def candidate():
    return {'title':TITLE,'url':URL,'description':''}

def page(text=''):
    return {'ok':True,'reason':'본문 조회됨','text':TITLE+' '+text,'links':[],'structured':[]}

class CloudTests(unittest.TestCase):
    def test_explicit_period_end_only(self):
        self.assertEqual(cloud.deadline('접수기간: 2026.10.01 ~ 2026.10.13 활동기간 2026.11.01')[0],'2026-10-13')
        self.assertEqual(cloud.deadline('접수기간: 2026.10.01부터')[0],'')
        self.assertEqual(cloud.deadline('마감: 10.13')[0],'')
        self.assertEqual(cloud.deadline('접수기간: 2026.10.01 ~ 10.13')[0],'')

    def test_metadata_job_deadline_and_event_date_not_confused(self):
        parsed = net.PageText(URL)
        parsed.feed('<script type="application/ld+json">'+json.dumps({'@graph':[{'@type':'JobPosting','title':'연구원 청년 인턴 채용','validThrough':'2026-10-20T18:00:00+09:00','hiringOrganization':{'name':'연구원'}}]})+'</script>연구원 청년 인턴 채용')
        row = cloud.extract(candidate(),{'ok':True,'text':'연구원 청년 인턴 채용','structured':parsed.structured,'links':[]})
        self.assertEqual(row['deadline'],'2026-10-20')
        self.assertTrue(row['verified'])
        p = page()
        p['structured']=[{'@type':'Event','endDate':'2026-11-20'}]
        self.assertFalse(cloud.extract(candidate(),p)['deadline_verified'])

    def test_mismatched_title_remains_uncertain(self):
        p = page('접수 마감 2026.10.13')
        p['text']='다른 게시판'
        row = cloud.extract(candidate(),p)
        self.assertFalse(row['verified'])
        self.assertFalse(row['deadline_verified'])

    def test_board_and_review_are_not_opportunity_notices(self):
        for url in ['https://example.org/','https://example.org/sub/list.php','https://example.org/selectYouthInternList.do','https://example.org/main.do']:
            self.assertIsNone(cloud.extract({**candidate(),'url':url},page('접수 마감 2026.10.13')))
        self.assertIsNone(cloud.extract({**candidate(),'title':'공기업 일경험 한달차 후기'},page()))

    @patch.dict('os.environ',{'NAVER_CLIENT_ID':'','NAVER_CLIENT_SECRET':''})
    @patch('net.get')
    def test_unrelated_web_search_results_filtered(self,get):
        get.return_value={'body':b'<rss><channel><item><title>2026 weather</title><link>https://example.org/weather</link></item><item><title>hackathon Korea</title><link>https://example.org/h</link></item></channel></rss>'}
        rows, status=cloud.search('test')
        self.assertTrue(status['ok'])
        self.assertEqual(len(rows),1)

    @patch('cloud_discovery.search',return_value=([],{'source':'test','ok':True}))
    @patch('net.pages')
    def test_expired_discovery_excluded(self,pages,search):
        pages.return_value={URL:page('접수 마감 2026.10.01')}
        rows,_,_=cloud.discover(TODAY,[candidate()],sources.plan(TODAY))
        self.assertEqual(rows,[])

    def test_unknown_organizer_next_year_same_url_is_new(self):
        row = cloud.extract(candidate(),page())
        newer={**row,'title':TITLE.replace('2026','2027'),'edition':'2027'}
        self.assertEqual(len(state.deduplicate([row,newer])),2)

    @patch('net.pages',return_value={URL:{'ok':False,'text':'','reason':'실패'}})
    def test_failed_reminder_refresh_disables_date(self,pages):
        row = cloud.extract(candidate(),page('접수 마감 2026.10.13'))
        self.assertFalse(cloud.refresh([row],TODAY)[0]['deadline_verified'])

    def test_cloud_does_not_call_codex_or_write_delivery_state_in_preview(self):
        row=cloud.extract(candidate(),page('접수 마감 2026.10.13'))
        stats={'web_search_ok':True,'provider':'test','queries':['test'],'successful_queries':1,'discovered_domains':1}
        with tempfile.TemporaryDirectory() as tmp, patch('sources.collect',return_value=([],[],sources.plan(TODAY))), patch('cloud_discovery.discover',return_value=([row],[],stats)), patch('verify.new_boards',return_value=[]), patch('delivery.pdf',side_effect=RuntimeError()), patch('discovery.discover',side_effect=AssertionError('Codex must not run')), patch('codex_client.CodexClient',side_effect=AssertionError('no login')):
            args=SimpleNamespace(cloud=True,data_dir=tmp,dry_run=True,force=False,no_llm=False,require_discovery=False)
            self.assertEqual(radar.run(args),0)
            self.assertFalse((Path(tmp)/'state'/'seen.json').exists())

if __name__=='__main__':
    unittest.main()
