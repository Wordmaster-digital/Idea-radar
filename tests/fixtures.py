"""tests/fixtures.py — 네트워크 없이 쓰는 합성 샘플과 항목 도우미.

실제 기사 문장을 옮기지 않고 전부 지어낸 내용이다.
"""
import json
from datetime import datetime, timedelta, timezone

NOW = datetime(2026, 9, 14, 23, 0, tzinfo=timezone.utc)

GNEWS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>구글 뉴스</title>
<item><title>동네 빨래방 예약 앱 '빨래톡' 등장 - 가상일보 - 가상일보</title>
<link>https://news.google.com/rss/articles/AAA?oc=5</link>
<pubDate>Mon, 14 Sep 2026 20:00:00 GMT</pubDate>
<source url="https://example.com">가상일보</source></item>
<item><title>사흘 전 기사 - 가상일보</title>
<link>https://news.google.com/rss/articles/BBB?oc=5</link>
<pubDate>Fri, 11 Sep 2026 20:00:00 GMT</pubDate>
<source url="https://example.com">가상일보</source></item>
</channel></rss>"""

LONG_DESC = "가상의 설명 문장입니다. " * 30

MEDIA_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>가상 매체</title>
<item><title>반려식물 돌봄 구독 '초록집' 출시</title>
<link>https://media.example/1?utm_source=feed</link>
<pubDate>Mon, 14 Sep 2026 10:00:00 +0900</pubDate>
<description><![CDATA[<p>{LONG_DESC}</p>]]></description></item>
<item><title>지난주 기사</title>
<link>https://media.example/2</link>
<pubDate>Mon, 07 Sep 2026 10:00:00 +0900</pubDate>
<description>짧은 설명</description></item>
</channel></rss>"""

APPSTORE_JSON = json.dumps({"feed": {"entry": [
    {"im:name": {"label": "빨래톡"},
     "im:releaseDate": {"label": "2026-08-01T00:00:00-07:00"},
     "id": {"label": "https://apps.apple.com/kr/app/id111", "attributes": {"im:id": "111"}},
     "summary": {"label": "동네 빨래방 예약 앱"}},
    {"im:name": {"label": "오래된앱"},
     "im:releaseDate": {"label": "2020-01-01T00:00:00-07:00"},
     "id": {"label": "https://apps.apple.com/kr/app/id222", "attributes": {"im:id": "222"}},
     "summary": {"label": "오래전에 나온 앱"}},
]}}, ensure_ascii=False)


def news_item(title, url, outlet="가상일보", source="gnews", hours_ago=1, desc=""):
    return {"source": source, "outlet": outlet, "title": title, "desc": desc, "url": url,
            "published": NOW - timedelta(hours=hours_ago), "chart_rank": None, "app_id": None}


def app_item(name="빨래톡", app_id="111", rank=3, days_ago=10):
    return {"source": "appstore", "outlet": f"앱스토어 무료 #{rank}", "title": name,
            "desc": "설명", "url": f"https://apps.apple.com/kr/app/id{app_id}",
            "published": NOW - timedelta(days=days_ago), "chart_rank": rank, "app_id": app_id}


def card(i, name, keep=True, traction="", stage=2, maker="스타트업"):
    return {"i": i, "keep": keep, "drop_reason": "", "name": name, "what": "하는 일 한 줄",
            "who": "대상 사용자", "maker": maker, "stage": stage, "stage_reason": "근거",
            "traction": traction, "kw": ["검색어1", "검색어2"]}


IDEA_TEMPLATE = {
    "title": "가칭", "one_liner": "한 줄", "axis": "축", "decision": "행동",
    "pain_scene": "장면", "first_users": "첫 사용자", "unexpected_users": "예상 밖",
    "data_sources": ["공공데이터 (확인 필요)"], "mvp_2weeks": "범위", "channel": "채널",
    "timing": "지금", "payer": "확인 필요", "incumbent_gap": "못 하는 것", "trap": "함정",
    "kr_duplicate": "불명", "kr_duplicate_basis": "근거 없음",
    "verdict": "GO", "verdict_reason": "이유",
}


def idea(i, verdict="GO", title="가칭"):
    return {**IDEA_TEMPLATE, "i": i, "verdict": verdict, "title": title}
