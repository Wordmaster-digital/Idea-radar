"""창업정보 선별 정책. 모든 출처와 LLM 미설정 실행에 공통 적용한다."""
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

DEFAULT_SOURCES = "hn,ph,fr,yc,platum,vsq"
FOUNDER_SOURCES = {"First Round Review", "Y Combinator"}
PRODUCT_SOURCES = {"PH", "HN", "GitHub"}

HARD_NEWS = re.compile(
    r"\b(who is hiring|obituary|lawsuit|election|stock market|"
    r"appoints|steps down|joins? .{0,35}(?:partner|board)|"
    r"new(?:est)? (?:managing|general) partner)\b|"
    r"부고|별세|인사발령|신임.{0,12}(?:선임|취임)|업무협약|협약\s*체결|주가|채용\s*공고", re.I)
EVENT_NEWS = re.compile(
    r"\b(acquires?|acquisition|raises?|raised|funding round|series [a-f]|"
    r"announces?|launch(?:es|ed)?|unveils?)\b|투자\s*유치|출시|실적\s*발표|상장|성황리|성료", re.I)
LESSON = re.compile(
    r"\b(how|lessons?|playbook|guide|case study|tactics?|strategy|strategies|"
    r"framework|mistakes?|learned|learning|validation|validate|pricing|monetiz\w*|"
    r"product.market.fit|bootstrapp\w*)\b|"
    r"사례|전략|노하우|실패|검증|수익모델|수익화|고객\s*확보|가격\s*책정|회고|교훈", re.I)
CUSTOMER = re.compile(
    r"\b(customers?|users?|buyers?|clients?|business(?:es)?|founders?|freelancers?|"
    r"shops?|stores?|restaurants?|teachers?|students?|parents?|patients?|"
    r"tenants?|landlords?|teams?|sellers?|creators?|travelers?|retailers?|"
    r"small.business(?:es)?|smbs?)\b|"
    r"고객|사용자|소상공인|자영업|소비자|학부모|반려|사업자|세입자|판매자|창업자", re.I)
PROBLEM = re.compile(
    r"\b(pain.points?|unmet|frustrat\w*|struggl\w*|workarounds?|manual|"
    r"time.consuming|problem|problems|need|needs|waste|waiting|churn)\b|"
    r"불편|미충족|고충|문제|수작업|이탈|대기시간|미수금|노쇼", re.I)
MODEL = re.compile(
    r"\b(business.model|pricing|monetiz\w*|revenue|mrr|arr|unit.economics|"
    r"willing.to.pay|paying.customers|bootstrapp\w*|profitab\w*)\b|"
    r"수익모델|사업모델|수익화|구독\s*모델|가격\s*책정|단위경제|유료\s*고객|월\s*매출", re.I)
GROWTH = re.compile(
    r"\b(product.market.fit|customer.discovery|customer.interviews?|"
    r"first.{0,12}customers?|customer.acquisition|go.to.market|"
    r"retention|conversion|validation|validate|distribution|onboarding)\b|"
    r"시장\s*검증|고객\s*인터뷰|고객\s*확보|유료\s*전환|시장\s*적합|초기\s*고객|리텐션", re.I)
SOLUTION = re.compile(
    r"\b(automate\w*|simplif\w*|manage\w*|track\w*|book\w*|match\w*|"
    r"schedul\w*|invoic\w*|save|saves|help|helps|reduce\w*|connect\w*)\b|"
    r"자동화|관리|예약|매칭|연결|절감|정산|추천|해결|탐색", re.I)
STARTUP = re.compile(r"\b(startups?|founders?|entrepreneurs?|saas|mvp|co.founders?)\b|창업|사업화|스타트업", re.I)
PROGRAM = re.compile(r"모집|신청|공모|교육|멘토링|지원사업|경진대회|해커톤|\b(apply|applications|mentoring|accelerator)\b", re.I)


def canonical_url(url):
    """추적 파라미터만 제거하고 식별용 쿼리(id 등)는 보존."""
    try:
        parsed = urlsplit(url or "")
    except ValueError:
        return ""
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return ""
    query = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in ("fbclid", "gclid")]
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, urlencode(query), ""))


def assess(item):
    """기사의 인기도가 아니라 사업에 쓸 수 있는 구체적 단서가 있는지 판단."""
    title = item.get("title", "")
    text = f"{title} {item.get('extra', '')}"
    lesson = LESSON.search(title)
    if HARD_NEWS.search(title):
        return None
    # 투자·출시 보도는 고객 확보/사업모델을 설명하는 제목일 때만 허용.
    product_demo = (item.get("source") in {"PH", "HN"}
                    and CUSTOMER.search(text) and SOLUTION.search(text)
                    and (item.get("source") == "PH" or title.lower().startswith("show hn:")))
    if EVENT_NEWS.search(title) and not (lesson or product_demo):
        return None
    if STARTUP.search(text) and PROGRAM.search(title):
        return "창업 프로그램", "신청 대상·지원 내용·일정을 원문에서 확인", 4
    if MODEL.search(text) and (lesson or STARTUP.search(text) or CUSTOMER.search(text)):
        return "사업모델·수익화", "가격·수익 구조와 지불 고객을 살펴볼 자료", 5
    if GROWTH.search(text) and (lesson or CUSTOMER.search(text) or STARTUP.search(text)):
        return "고객 확보·검증", "고객 발견·검증·획득 방법을 살펴볼 자료", 5
    if PROBLEM.search(text) and CUSTOMER.search(text):
        return "고객 문제·수요", "반복되는 고객 문제와 현재 대안을 확인할 자료", 5
    if lesson and (STARTUP.search(text) or item.get("source") in FOUNDER_SOURCES):
        return "창업 실행자료", "창업자가 적용할 실행 과정과 교훈을 살펴볼 자료", 4
    if item.get("source") in PRODUCT_SOURCES and CUSTOMER.search(text) and SOLUTION.search(text):
        return "서비스·제품 사례", "대상 고객·해결 방식·기존 대안을 비교할 자료", 3
    return None


def select_startup_items(items):
    selected, seen = [], set()
    for item in items:
        url = canonical_url(item.get("url"))
        decision = assess(item)
        if not url or url in seen or decision is None:
            continue
        seen.add(url)
        kind, reason, priority = decision
        selected.append({**item, "url": url, "info_type": kind,
                         "startup_reason": item.get("startup_reason") or reason,
                         "startup_priority": priority})
    return selected


def needs_market_check(item):
    # 일반 창업 조언과 모집공고는 서비스 중복 검사의 대상이 아니다.
    return item.get("info_type") == "서비스·제품 사례"


def chunk_text(text, limit=1900):
    chunks = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit) + 1 or limit
        chunks.append(text[:cut])
        text = text[cut:]
    if text:
        chunks.append(text)
    return chunks
