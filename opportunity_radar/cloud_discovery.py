"""Hosted discovery without a PC or ChatGPT login. Public web RSS + optional NAVER API."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
import html
import json
import os
import re
from urllib.parse import quote, urlsplit
import xml.etree.ElementTree as ET
import net
import sources
import state
import verify
from discovery import FIELDS

TERMS = {'해커톤': ('해커톤','메이커톤','hackathon'), '공모전': ('공모','경진대회','콘테스트'),
         '인턴': ('인턴','intern'), '일경험': ('일경험','현장실습'),
         '채용': ('신입','채용','체험형'), '대외활동': ('대외활동','서포터즈','참가자','참여자','모집','활동')}
DATE = re.compile(r'(?<!\d)(20\d{2})\s*[./년-]\s*(\d{1,2})\s*[./월-]\s*(\d{1,2})(?!\d)')
LABEL = re.compile(r'(마감(?:일시|일|기한)?|접수기간|신청기간|모집기간|응모기간|공모기간)\s*[:：]?\s*(.{0,160})')


def plain(text):
    return re.sub(r'\s+', ' ', html.unescape(re.sub('<[^>]+>',' ', str(text)))).strip()


def category(text):
    text = text.casefold()
    return next((name for name, terms in TERMS.items() if any(t in text for t in terms)), '')


def queries(plan):
    return ['해커톤 모집','공모전 모집','대외활동 모집','인턴 채용','일경험 모집','신입 체험형 채용',
            plan['region'] + ' 청년 모집', plan['field'] + ' 공모전',
            '산학협력단 교외 공모전', '재단 참가자 모집', '연구원 인턴 모집', '협회 공모전 모집']


def search(query):
    cid, secret = os.environ.get('NAVER_CLIENT_ID'), os.environ.get('NAVER_CLIENT_SECRET')
    provider = '네이버 웹 문서' if cid and secret else 'Bing 공개 웹 RSS'
    try:
        if cid and secret:
            url = 'https://openapi.naver.com/v1/search/webkr.json?display=20&query=' + quote(query)
            # API credentials are sent only to the official endpoint; never follow a redirect with them.
            from urllib.request import Request, build_opener, HTTPRedirectHandler
            class NoRedirect(HTTPRedirectHandler):
                def redirect_request(self,*args,**kwargs):
                    raise ValueError('API 이동 거부')
            request = Request(url,headers={'X-Naver-Client-Id':cid,'X-Naver-Client-Secret':secret,'User-Agent':net.AGENT})
            with build_opener(NoRedirect()).open(request,timeout=12) as response:
                data = json.loads(response.read(250_000).decode('utf-8'))
            raw = [{'title':plain(r.get('title','')), 'url':r.get('link',''),
                    'description':plain(r.get('description',''))} for r in data.get('items',[])[:20]]
        else:
            url = 'https://www.bing.com/search?format=rss&mkt=ko-KR&setlang=ko&cc=kr&q=' + quote(query)
            tree = ET.fromstring(net.get(url)['body'])
            raw = [{'title':plain(r.findtext('title','')), 'url':r.findtext('link',''),
                    'description':plain(r.findtext('description',''))} for r in tree.findall('./channel/item')[:10]]
        relevant = []
        for row in raw:
            if category(row['title'] + ' ' + row['description']) and row['url'].startswith(('http://','https://')):
                relevant.append({**row,'source':provider,'search_query':query})
        status = {'source':provider + ': ' + query,'ok':True,'count':len(relevant),
                  'raw_count':len(raw), 'reason':'검색 조회됨 · 모집 주제 후보 ' + str(len(relevant)) + '건'}
        return relevant, status
    except Exception:
        return [], {'source':provider + ': ' + query,'ok':False,'reason':'웹 검색 조회 실패'}


def deadline(text):
    """Only an explicitly year-qualified ending date, never a guessed year/time."""
    for match in LABEL.finditer(text):
        label, snippet = match[1], match[2]
        # Stop at another recruitment field, avoiding an unrelated event date.
        snippet = re.split(r'지원자격|참가대상|모집대상|활동기간|시상|문의|신청방법|결과발표|발표일',snippet)[0]
        dates = []
        for d in DATE.finditer(snippet):
            try:
                dates.append((date(*map(int,d.groups())),d.start(),d.end()))
            except ValueError:
                pass
        if not dates:
            continue
        if len(dates) == 1 and '마감' not in label:
            # A single date in a period may be its opening, not closing date.
            continue
        chosen = dates[-1]
        if len(dates) == 1 and any(t in snippet[chosen[2]:] for t in ('~','∼','부터')):
            continue
        evidence = (label + ' ' + snippet[:100]).strip()
        return chosen[0].isoformat(), evidence
    return '', ''


def structured_job(values):
    def walk(value):
        if isinstance(value,list):
            for row in value:
                yield from walk(row)
        elif isinstance(value,dict):
            typ = value.get('@type',[])
            if typ == 'JobPosting' or isinstance(typ,list) and 'JobPosting' in typ:
                yield value
            if '@graph' in value:
                yield from walk(value['@graph'])
    return next(walk(values),None)


def field(text,label):
    match = re.search(r'(?:' + label + r')\s*[:：]?\s*(.{8,160})',text)
    if not match:
        return '확인 필요'
    value = re.split(r'홈페이지|공유하기|스크랩|지원자격|모집인원|근무지역|근무지|모집직무|전형절차|지원기간|접수기간|시상내역|활동혜택|활동지역|개인정보|더보기',match[1])[0].strip()
    if not value or any(t in value for t in ('커뮤니티','조회','추천','광고')):
        return '확인 필요'
    return value[:80]


def extract(candidate,page):
    title = re.sub(r'^(추천|마감임박|신규)\s*','',plain(candidate['title']))[:180]
    title = re.sub(r'^(문학[•·]문예|디자인[•·]캐릭터|사진[•·]영상|기획[•·]아이디어)\s+','',title)
    if len(title) < 7:
        return None
    host = urlsplit(candidate['url']).hostname or ''
    path = urlsplit(candidate['url']).path
    # Portals, search pages and category boards are discovery sources, not individual notices.
    if path in ('','/') or re.search(r'(?:/list(?:/|\.|$)|listpage|select\w*list|searchjob|recruitsearch|main\.(?:do|php)|/theme/|/product/)',path,re.I):
        return None
    if host.endswith(('dcinside.com','reddit.com')) or re.search(r'후기|회고|채용정보시스템|채용정보 \||채용공고 \d+건|모집공고 <|신입·인턴 채용관|^홈\s*[|｜]|채용 홈페이지|참여자 모집 <|공모전 찾는 방법|맞춤법 검사|출품해도|공모전 세금|수상하게 되면',title):
        return None
    text = page.get('text','')
    job = structured_job(page.get('structured',[]))
    if job and isinstance(job.get('title'),str):
        title = plain(job['title'])[:180]
    kind = (category(title) or '채용') if job else category(title)
    if '채용요건' in title and re.search(r'\d+기 모집',title):
        kind = '대외활동'
    if not kind:
        return None
    if any(t in title for t in ('접수종료','모집종료','채용종료','기업 지원사업','사업화 지원금')):
        return None
    organization = '확인 필요'
    closing, closing_evidence = deadline(text)
    if job:
        organization = plain((job.get('hiringOrganization') or {}).get('name','확인 필요')) if isinstance(job.get('hiringOrganization'),dict) else '확인 필요'
        value = job.get('validThrough','')
        if isinstance(value,str) and re.match(r'^20\d{2}-\d{2}-\d{2}',value):
            try:
                date.fromisoformat(value[:10])
                closing = value[:10]
                closing_evidence = '원문의 JobPosting.validThrough: ' + value[:40]
            except ValueError:
                pass
    row = {k:'' for k in FIELDS}
    row.update(title=title,organizer=organization,edition=next(iter(re.findall(r'20\d{2}',title)),''),
        category=kind,url=net.canonical(candidate['url']),deadline=closing,
        deadline_text=closing_evidence or '접수 마감일 확인 필요',deadline_evidence=closing_evidence,
        eligibility=field(text,'지원자격|참가대상|모집대상|응모자격'),
        region=field(text,'활동지역|활동장소|근무지|근무지역|개최장소'),
        benefit=field(text,'시상내역|활동혜택|참여혜택|급여|수당'),
        summary=plain(job.get('description',''))[:120] if job else '공개 웹에서 발견한 모집 공고. 지원 조건은 아래 원문에서 확인하세요.',
        status='unknown')
    visible = bool(page.get('ok') and state.norm(title) in state.norm(text))
    row.update(verified=visible,deadline_verified=bool(closing) and visible,
               verification=('원문 제목 대조 · 접수 마감 원문 추출' if closing else '원문 제목 대조 · 마감 추가 확인') if visible else page.get('reason','본문 확인 필요') + ' · 조건 재확인 필요')
    if visible:
        location = text.find(title)
        row['evidence'] = text[location:location+110] if location >= 0 else title
        row['status'] = 'open' if closing else 'unknown'
    for link in page.get('links',[]):
        if re.fullmatch(r'\s*(공지사항|채용공고|모집공고|Notice|채용정보)\s*',link['title'],re.I):
            row['board_url'] = link['url']
            break
    return row


def discover(today,candidates,plan):
    found, coverage = [], []
    with ThreadPoolExecutor(max_workers=4) as pool:
        for rows,status in pool.map(search,queries(plan)):
            found.extend(rows)
            coverage.append(status)
    found.extend(candidates)
    unique = {}
    for candidate in found:
        url = candidate.get('url','')
        if url.startswith(('https://','http://')) and category(candidate['title'] + ' ' + candidate.get('description','')):
            unique.setdefault(net.canonical(url),candidate)
    # Give every category access to the fetch budget, rather than filling it with one aggregator.
    buckets = {k:[] for k in TERMS}
    for candidate in unique.values():
        kind = category(candidate['title'] + ' ' + candidate.get('description',''))
        buckets[kind].append(candidate)
    balanced = []
    for index in range(60):
        for bucket in buckets.values():
            if index < len(bucket) and len(balanced) < 40:
                balanced.append(bucket[index])
    fetched = net.pages([r['url'] for r in balanced])
    rows = []
    extra = {}
    for candidate in balanced:
        page = fetched[candidate['url']]
        row = extract(candidate,page)
        if row:
            if not row['deadline'] or row['deadline'] >= today.isoformat():
                rows.append(row)
        elif page.get('ok'):
            for link in page.get('links',[]):
                if category(link['title']) and len(link['title']) >= 8 and link['url'] not in fetched:
                    extra.setdefault(link['url'],{**link,'source':'새로 발견한 게시판','description':''})
    second = list(extra.values())[:20]
    additional = net.pages([r['url'] for r in second])
    for candidate in second:
        row = extract(candidate,additional[candidate['url']])
        if row and (not row['deadline'] or row['deadline'] >= today.isoformat()):
            rows.append(row)
    broad_ok = any(r['ok'] for r in coverage)
    return rows, coverage, {'queries':queries(plan),'successful_queries':sum(r['ok'] for r in coverage),
            'discovered_domains':len({urlsplit(r['url']).hostname for r in found}),
            'candidate_count':len(unique),'web_search_ok':broad_ok,
            'provider':'NAVER' if os.environ.get('NAVER_CLIENT_ID') and os.environ.get('NAVER_CLIENT_SECRET') else 'Bing RSS'}


def refresh(rows,today):
    fetched = net.pages([r['url'] for r in rows])
    fresh = []
    for previous in rows:
        row = extract({'title':previous['title'],'url':previous['url']},fetched[previous['url']])
        if row:
            fresh.append(row)
        else:
            fresh.append({**previous,'verified':False,'deadline_verified':False,'verification':'원문 재조회 실패 · 자동 마감 알림 보류'})
    return fresh
