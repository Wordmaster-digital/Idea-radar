#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""kr_sources.py — 국내에서 새로 나온 아이템을 모은다.

소스 세 가지를 쓴다.
  - 구글 뉴스 검색 RSS: 개인·소규모 팀이 만든 앱은 '출시'보다 '등장·화제'로 보도된다.
  - 국내 스타트업 매체 RSS
  - 애플 앱스토어 한국 무료 차트: 최근 출시된 앱이 순위에 오르면 화제 신호다.

모든 함수는 fetcher를 인자로 받아 테스트에서 네트워크 없이 돌린다.
"""

import json
import math
import sys
import urllib.parse
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from daily_digest import clean, fetch, parse_xml

GNEWS_QUERIES = ["앱 출시", "서비스 론칭", "앱 등장", "앱 화제", "미니앱 출시"]
GNEWS_URL = "https://news.google.com/rss/search?q={q}&hl=ko&gl=KR&ceid=KR:ko"

MEDIA_FEEDS = [
    ("벤처스퀘어", "https://www.venturesquare.net/feed"),
    ("아웃스탠딩", "https://outstanding.kr/feed"),
    ("스타트업레시피", "https://www.startuprecipe.co.kr/feed"),
    ("플래텀", "https://platum.kr/feed"),
    ("비석세스", "https://besuccess.com/feed"),
    ("스타트업투데이", "https://www.startuptoday.kr/rss/allArticle.xml"),
]

APPSTORE_URL = "https://itunes.apple.com/kr/rss/topfreeapplications/limit=100/json"
APP_MAX_AGE_DAYS = 120
DESC_LIMIT = 300
SOURCE_COUNT = len(GNEWS_QUERIES) + len(MEDIA_FEEDS) + 1


def parse_date(text):
    """RSS·JSON 날짜 문자열을 UTC datetime으로. 해석하지 못하면 None."""
    if not text:
        return None
    text = text.strip()
    try:
        dt = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def strip_outlet(title, outlet):
    """구글 뉴스 제목 끝의 ' - 매체명'을 반복해서 뗀다."""
    title = (title or "").strip()
    suffix = f" - {outlet}" if outlet else ""
    while suffix and title.endswith(suffix):
        title = title[: -len(suffix)].rstrip()
    return title


def _fail(errors, name, exc):
    print(f"[{name}] 실패: {exc}", file=sys.stderr)
    if errors is not None:
        errors.append(name)


def _within(published, now, hours):
    return published is not None and now - published <= timedelta(hours=hours)


def _item(source, outlet, title, url, published, desc="", chart_rank=None, app_id=None):
    return {"source": source, "outlet": outlet, "title": title, "desc": desc, "url": url,
            "published": published, "chart_rank": chart_rank, "app_id": app_id}


def gnews(now, hours=24, fetcher=fetch, errors=None, queries=GNEWS_QUERIES):
    days = max(1, math.ceil(hours / 24))
    out = []
    for query in queries:
        url = GNEWS_URL.format(q=urllib.parse.quote(f"{query} when:{days}d"))
        try:
            root = parse_xml(fetcher(url))
        except Exception as e:
            _fail(errors, f"구글뉴스:{query}", e)
            continue
        for node in root.findall(".//item"):
            outlet = (node.findtext("source") or "").strip()
            title = strip_outlet(clean(node.findtext("title"), 200), outlet)
            link = (node.findtext("link") or "").strip()
            published = parse_date(node.findtext("pubDate"))
            if title and link and _within(published, now, hours):
                out.append(_item("gnews", outlet, title, link, published))
    return out


def media(now, hours=24, fetcher=fetch, errors=None, feeds=MEDIA_FEEDS):
    out = []
    for outlet, url in feeds:
        try:
            root = parse_xml(fetcher(url))
        except Exception as e:
            _fail(errors, outlet, e)
            continue
        for node in root.findall(".//item"):
            title = clean(node.findtext("title"), 200)
            link = (node.findtext("link") or "").strip()
            published = parse_date(node.findtext("pubDate") or node.findtext("date"))
            if title and link and _within(published, now, hours):
                out.append(_item("media", outlet, title, link, published,
                                 desc=clean(node.findtext("description"), DESC_LIMIT)))
    return out


def appstore(now, fetcher=fetch, errors=None, max_age_days=APP_MAX_AGE_DAYS):
    try:
        entries = json.loads(fetcher(APPSTORE_URL))["feed"]["entry"]
    except Exception as e:
        _fail(errors, "앱스토어", e)
        return []
    out = []
    for rank, entry in enumerate(entries, 1):
        try:
            name = clean(entry["im:name"]["label"], 100)
            released = parse_date(entry["im:releaseDate"]["label"])
            app_id = entry["id"]["attributes"]["im:id"]
            link = entry["id"]["label"]
        except (KeyError, TypeError):
            continue
        if not _within(released, now, max_age_days * 24):
            continue
        out.append(_item("appstore", f"앱스토어 무료 #{rank}", name, link, released,
                         desc=clean((entry.get("summary") or {}).get("label"), DESC_LIMIT),
                         chart_rank=rank, app_id=app_id))
    return out


def collect(now, hours=24, fetcher=fetch):
    """모든 소스를 모은다. 반환: (항목 목록, 실패한 소스 이름 목록)."""
    errors = []
    items = gnews(now, hours, fetcher, errors)
    items += media(now, hours, fetcher, errors)
    items += appstore(now, fetcher, errors)
    return items, errors
