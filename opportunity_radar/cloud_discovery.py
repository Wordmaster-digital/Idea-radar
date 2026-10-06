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
import ranking
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


def queries(plan, profile=None):
    broad = ['해커톤 모집','공모전 모집','대외활동 모집','인턴 채용','일경험 모집','신입 체험형 채용',
            plan['region'] + ' 청년 모집', plan['field'] + ' 공모전',
            '산학협력단 교외 공모전', '재단 참가자 모집', '연구원 인턴 모집', '협회 공모전 모집']
    if profile:
        broad += ['KOTRA 무역 해외마케팅 인턴 모집', '글로벌 마케팅 무역 일경험 모집',
                  '마케팅 기획 브랜드 공모전 모집', '영어 대학생 서포터즈 모집',
                  '세종 대전 마케팅 일경험 모집', '서울 경기 해외영업 마케팅 인턴 채용']
    return broad


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


FIELD_BOUNDARY = r'주최(?:/주관)?|주관|기업명|기관명|회사명|지원자격|참가대상|모집대상|응모자격|자격요건|모집인원|근무지역|근무지|모집직무|모집분야|학력|경력|고용형태|채용형태|주요업무|담당업무|우대사항|마감일|시작일|전형절차|지원기간|접수기간|시상내역|활동혜택|활동지역|활동장소|개최장소|참여혜택|문의처|문의|홈페이지|공유하기|스크랩|개인정보|더보기'


def field(text,label):
    match = re.search(r'(?:' + label + r')\s*[:：]?\s*(.{2,160})',text)
    if not match:
        return '확인 필요'
    value = re.split(FIELD_BOUNDARY,match[1])[0].strip(' :：|')
    if not value or any(t in value for t in ('커뮤니티','조회','추천','광고')):
        return '확인 필요'
    return value[:80]


def context(text, title, job=None):
    if job and job.get('description'):
        return plain(job['description'])[:3000]
    position = text.find(title)
    if position < 0:
        return ''
    value = text[position:position+3000]
    return re.split(r'추천\s*공고|관련\s*공고|다른\s*공고|인기\s*공고|함께\s*보면|합격\s*자소서|합격\s*스펙|허위[·ㆍ]과장|이\s*공고를\s*스크랩|이\s*공고\s*조회자가|담당자\s*Q',value)[0]


def extract(candidate,page):
    title = re.sub(r'^(추천|마감임박|신규)\s*','',plain(candidate['title']))[:180]
    title = re.sub(r'^(문학[•·]문예|디자인[•·]캐릭터|사진[•·]영상|기획[•·]아이디어)\s+','',title)
    title = re.split(r'\s+주최\s*[:：]',title)[0]
    title = re.sub(r'^\d+[.)]\s+','',title)
    title = re.sub(r'\s*(?:\.{3}|…)\s*$','',title)
    if len(title) < 7:
        return None
    host = urlsplit(candidate['url']).hostname or ''
    path = urlsplit(candidate['url']).path
    # Portals, search pages and category boards are discovery sources, not individual notices.
    if path in ('','/') or path in ('/contest','/activity') and not urlsplit(candidate['url']).query or re.search(r'(?:/list(?:/|\.|$)|listpage|select\w*list|searchjob|recruitsearch|main\.(?:do|php)|/theme/|/product/)',path,re.I):
        return None
    if host.endswith(('dcinside.com','reddit.com','namu.wiki','wikipedia.org')) or re.search(r'후기|회고|예고편|한국 영화|홈페이지 빌더|대행서비스 안내|홍보[·ㆍ]운영대행|채용정보시스템|채용정보 \||채용공고 \d+건|모집공고 <|신입·인턴 채용관|^홈\s*[|｜]|채용 홈페이지|참여자 모집 <|공모전 찾는 방법|맞춤법 검사|출품해도|공모전 세금|수상하게 되면',title):
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
    notice_context = context(text,title)
    relevant = notice_context or context(text,title,job)
    if job:
        metadata_context = context(text,title,job)
        if metadata_context and metadata_context not in relevant:
            relevant = (relevant + ' ' + metadata_context)[:3500]
    # Only the notice's own bounded content supplies ranking and field evidence.
    details = relevant or title
    organization = field(details,'주최(?:/주관)?|주관|기업명|회사명|기관명')
    closing, closing_evidence = deadline(details)
    if job:
        organization = plain((job.get('hiringOrganization') or {}).get('name','확인 필요')) if isinstance(job.get('hiringOrganization'),dict) else '확인 필요'
        value = job.get('validThrough','')
        rolling = re.search(r'채용\s*시\s*마감|채용\s*시까지|상시\s*(?:채용|모집)',details)
        if not rolling and isinstance(value,str) and re.match(r'^20\d{2}-\d{2}-\d{2}',value) and not closing:
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
        eligibility=field(details,'지원자격|참가대상|모집대상|응모자격|자격요건'),
        region=field(details,'활동지역|활동장소|근무지역|근무지|개최장소|대회지역'),
        benefit=field(details,'시상내역|활동혜택|참여혜택|급여|수당'),
        summary=plain(job.get('description',''))[:120] if job else '공개 웹에서 발견한 모집 공고. 지원 조건은 아래 원문에서 확인하세요.',
        status='unknown')
    row['context'] = relevant
    if job and rolling and not closing:
        row['deadline_text'] = '채용 시 마감·상시 · 정확한 종료일은 원문 확인'
    if job:
        locations = job.get('jobLocation',[])
        if isinstance(locations,dict):
            locations = [locations]
        addresses = []
        for place in locations if isinstance(locations,list) else []:
            address = place.get('address',{}) if isinstance(place,dict) else {}
            if isinstance(address,dict):
                addresses.extend(str(address.get(k,'')) for k in ('addressRegion','addressLocality'))
            elif isinstance(address,str):
                addresses.append(address)
        if any(addresses):
            row['region'] = ' '.join(addresses)[:80]
        if job.get('jobLocationType') == 'TELECOMMUTE':
            row['region'] = '온라인·원격'
    visible = bool(page.get('ok') and state.norm(title) in state.norm(text))
    row.update(verified=visible,deadline_verified=bool(closing) and visible,
               verification=('원문 제목 대조 · 접수 마감 원문 추출' if closing else '원문 제목 대조 · 마감 추가 확인') if visible else page.get('reason','본문 확인 필요') + ' · 조건 재확인 필요')
    if visible:
        row['evidence'] = relevant[:110] if relevant else title
        row['status'] = 'open' if closing else 'unknown'
        if re.search(r'모집이\s*종료되었습니다|접수가\s*종료되었습니다|접수\s*마감되었습니다|공고가\s*마감되었습니다',details):
            row['status'] = 'closed'
    for link in page.get('links',[]):
        if re.fullmatch(r'\s*(공지사항|채용공고|모집공고|Notice|채용정보)\s*',link['title'],re.I):
            row['board_url'] = link['url']
            break
    return row


def discover(today,candidates,plan,profile=None):
    found, coverage = [], []
    with ThreadPoolExecutor(max_workers=4) as pool:
        for rows,status in pool.map(search,queries(plan,profile)):
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
    if profile:
        focused = sorted(unique.values(),key=lambda r:-ranking.discover_priority(r,profile))
        domains = {}
        for candidate in focused:
            host = urlsplit(candidate['url']).hostname
            if ranking.discover_priority(candidate,profile) <= 0 or len(balanced) >= 20:
                break
            if domains.get(host,0) >= 8:
                continue
            balanced.append(candidate)
            domains[host] = domains.get(host,0)+1
    picked = {r['url'] for r in balanced}
    for index in range(60):
        for bucket in buckets.values():
            if index < len(bucket) and len(balanced) < 40 and bucket[index]['url'] not in picked:
                balanced.append(bucket[index])
                picked.add(bucket[index]['url'])
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
    second = list(extra.values())
    if profile:
        second.sort(key=lambda r:-ranking.discover_priority(r,profile))
    second = second[:20]
    additional = net.pages([r['url'] for r in second])
    for candidate in second:
        row = extract(candidate,additional[candidate['url']])
        if row and (not row['deadline'] or row['deadline'] >= today.isoformat()):
            rows.append(row)
    broad_ok = any(r['ok'] for r in coverage)
    return rows, coverage, {'queries':queries(plan,profile),'successful_queries':sum(r['ok'] for r in coverage),
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
