#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
daily_digest.py — 아이디어 자동 수집 + 한국 중복 대조 + 디스코드 발송

수집 소스 (전부 무료, 인증 불필요, 실제 점수 포함):
  - Hacker News   : Algolia API      → points
  - GitHub        : 공식 Search API  → stars (최근 생성 저장소)
  - Lobsters      : hottest.json     → score
  - Dev.to        : 공식 API         → reactions
  - Product Hunt  : 공식 RSS         → 점수 없음 (GraphQL은 토큰 필요)

  ※ Reddit은 제외. 클라우드 IP를 403으로 차단한다.
    쓰려면 OAuth 앱 등록이 필요하고, 그마저 정책 변경에 취약하다.

대조:
  - 각 항목에서 키워드를 뽑아 한국 앱스토어(iTunes Search API)에 조회
  - 유사 앱이 있으면 🔴, 없으면 🟢
  - 한계: 영어 단어 매칭이라 오탐/누락이 있다. 1차 필터일 뿐 판정기가 아니다.

사용법:
    export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."
    python3 daily_digest.py
    python3 daily_digest.py --dry-run          # 화면 출력만
    python3 daily_digest.py --top 25           # 상위 N건만
    python3 daily_digest.py --only-gaps        # 🟢만
    python3 daily_digest.py --sources hn,gh    # 소스 선택
"""

import argparse
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

try:
    import llm_enrich
except ImportError:
    llm_enrich = None

TIMEOUT = 25
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

ITUNES = "https://itunes.apple.com/search"

NOISE = re.compile(
    r"\b(who is hiring|hiring|ask hn:|tell hn:|died|dies|obituary|rip\b|"
    r"lawsuit|acquires|acquisition|funding round|raises \$|series [abc]\b)",
    re.I,
)
STOPWORDS = {
    "the", "a", "an", "and", "or", "for", "with", "your", "you", "our", "that",
    "this", "from", "into", "using", "use", "how", "why", "what", "new", "open",
    "source", "free", "best", "just", "get", "make", "made", "build", "built",
    "show", "hn", "ask", "app", "tool", "api", "web", "all", "one", "can", "now",
    "its", "his", "her", "was", "are", "has", "have", "not", "but", "out", "via",
    "when", "who", "where", "about", "more", "than", "over", "some", "any",
}


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read().decode("utf-8", "replace")


def fetch_json(url):
    return json.loads(fetch(url))


def parse_xml(text):
    """피드 앞에 BOM이나 공백, HTML 주석이 섞여 있어도 파싱한다."""
    text = text.lstrip("\ufeff \t\r\n")
    i = text.find("<?xml")
    if i > 0:
        text = text[i:]
    else:
        j = min([k for k in (text.find("<rss"), text.find("<feed"),
                             text.find("<rdf:RDF")) if k >= 0] or [0])
        text = text[j:]
    try:
        return ET.fromstring(text)
    except ET.ParseError:
        pass
    # 제어문자 제거
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    try:
        return ET.fromstring(text)
    except ET.ParseError:
        pass
    # 피드에 <script>가 섞여 XML을 깨뜨리는 경우 (Springwise 등)
    text = re.sub(r"<script\b.*?</script>", "", text, flags=re.S | re.I)
    text = re.sub(r"&(?!(?:[a-zA-Z][a-zA-Z0-9]{1,7}|#\d{1,7}|#x[0-9a-fA-F]{1,6});)", "&amp;", text)
    try:
        return ET.fromstring(text)
    except ET.ParseError:
        pass
    # 최후 수단: title/link/pubDate만 정규식으로 긁어 최소 트리를 만든다
    root = ET.Element("rss")
    chan = ET.SubElement(root, "channel")
    for blk in re.findall(r"<item[\s>].*?</item>", text, re.S | re.I)[:40]:
        def pick(tag):
            m = re.search(rf"<{tag}[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{tag}>",
                          blk, re.S | re.I)
            return re.sub(r"<[^>]+>", " ", m.group(1)).strip() if m else ""
        it = ET.SubElement(chan, "item")
        for tag in ("title", "link", "description", "pubDate"):
            ET.SubElement(it, tag).text = pick(tag)
    if not list(chan):
        raise ET.ParseError("피드에서 item을 찾지 못함")
    return root


def _age_hours(datestr):
    """RSS pubDate를 시간 단위 나이로. 파싱 실패 시 24로 가정."""
    if not datestr:
        return 24.0
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(datestr.strip())
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except Exception:
        try:
            dt = datetime.fromisoformat(datestr.strip().replace("Z", "+00:00"))
        except Exception:
            return 24.0
    if dt.tzinfo is None:                      # 타임존 없는 피드 대응
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 3600)


def strip_utm(u):
    return re.sub(r"[?&]utm_[^&]*", "", u or "").rstrip("?&")


def clean(s, n=150):
    s = re.sub(r"<[^>]+>", " ", html.unescape(s or ""))
    return re.sub(r"\s+", " ", s).strip()[:n]


# =============================================================== 수집 소스

def src_hn(limit=15, min_points=150, days=2):
    """Hacker News — Algolia API. 요청 1회로 끝나고 points가 들어온다."""
    since = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())
    nf = urllib.parse.quote(f"points>{min_points},created_at_i>{since}")
    url = ("https://hn.algolia.com/api/v1/search_by_date?tags=story"
           f"&numericFilters={nf}&hitsPerPage={limit * 3}")
    try:
        hits = fetch_json(url).get("hits", [])
    except Exception as e:
        print(f"[HN] 실패: {e}", file=sys.stderr)
        return []
    out = []
    for h in hits:
        title = h.get("title") or ""
        if not title or NOISE.search(title):
            continue
        out.append({
            "source": "HN",
            "title": title,
            "score": h.get("points", 0),
            "unit": "pt",
            "url": h.get("url") or f"https://news.ycombinator.com/item?id={h['objectID']}",
            "extra": f"{h.get('num_comments', 0)} comments",
            "age_h": max(0.0, (datetime.now(timezone.utc).timestamp()
                               - h.get("created_at_i", 0)) / 3600),
        })
        if len(out) >= limit:
            break
    return out


def src_github(limit=15, days=14, min_stars=200):
    """GitHub — 공식 Search API. 최근 생성된 저장소를 별 순으로."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    q = urllib.parse.quote(f"created:>{since} stars:>{min_stars}")
    url = (f"https://api.github.com/search/repositories?q={q}"
           f"&sort=stars&order=desc&per_page={limit}")
    try:
        items = fetch_json(url).get("items", [])
    except Exception as e:
        print(f"[GitHub] 실패: {e}", file=sys.stderr)
        return []
    return [{
        "source": "GitHub",
        "title": f"{i['full_name']} — {clean(i.get('description'), 80)}",
        "score": i.get("stargazers_count", 0),
        "unit": "★",
        "url": i.get("html_url", ""),
        "extra": clean(i.get("description"), 120),
        "age_h": _age_hours(i.get("created_at")),
    } for i in items]


def src_lobsters(limit=10, min_score=15):
    """Lobsters — hottest.json. HN보다 기술 편중이지만 노이즈가 적다."""
    try:
        items = fetch_json("https://lobste.rs/hottest.json")
    except Exception as e:
        print(f"[Lobsters] 실패: {e}", file=sys.stderr)
        return []
    out = []
    for i in items:
        if i.get("score", 0) < min_score or NOISE.search(i.get("title", "")):
            continue
        out.append({
            "source": "Lobsters",
            "title": i.get("title", ""),
            "score": i.get("score", 0),
            "unit": "pt",
            "url": i.get("url") or i.get("comments_url", ""),
            "extra": ", ".join(i.get("tags", [])[:3]),
        })
        if len(out) >= limit:
            break
    return out


def src_devto(limit=10, min_reactions=30):
    """Dev.to — 공식 API. 개발자가 실제로 만든 것 위주."""
    try:
        items = fetch_json(f"https://dev.to/api/articles?top=2&per_page={limit * 3}")
    except Exception as e:
        print(f"[Dev.to] 실패: {e}", file=sys.stderr)
        return []
    out = []
    for i in items:
        r = i.get("positive_reactions_count", 0)
        if r < min_reactions:
            continue
        out.append({
            "source": "Dev.to",
            "title": i.get("title", ""),
            "score": r,
            "unit": "♥",
            "url": i.get("url", ""),
            "extra": ", ".join(i.get("tag_list", [])[:3]),
        })
        if len(out) >= limit:
            break
    return out


def src_producthunt(limit=14):
    """Product Hunt — 공식 피드(Atom). 투표 수는 없지만 비개발 아이디어가 가장 많다."""
    try:
        root = parse_xml(fetch("https://www.producthunt.com/feed"))
    except Exception as e:
        print(f"[ProductHunt] 실패: {e}", file=sys.stderr)
        return []
    ns = "{http://www.w3.org/2005/Atom}"
    nodes = root.findall(".//item") or root.findall(f".//{ns}entry")
    out = []
    for n in nodes[:limit * 2]:
        if n.tag.endswith("entry"):
            title = (n.findtext(f"{ns}title") or "").strip()
            le = n.find(f"{ns}link")
            link = le.get("href") if le is not None else ""
            desc = n.findtext(f"{ns}summary") or n.findtext(f"{ns}content") or ""
        else:
            title = (n.findtext("title") or "").strip()
            link = (n.findtext("link") or "").strip()
            desc = n.findtext("description") or ""
        if not title:
            continue
        d = re.sub(r"\s*Discussion\s*\|\s*Link\s*$", "", clean(desc, 160))
        out.append({"source": "PH", "title": title, "score": None, "unit": "",
                    "url": link, "extra": d})
        if len(out) >= limit:
            break
    return out


def _generic_rss(name, url, source, limit, skip=None):
    try:
        root = parse_xml(fetch(url))
    except Exception as e:
        print(f"[{name}] 실패: {e}", file=sys.stderr)
        return []
    out = []
    for it in root.findall(".//item")[:limit * 2]:
        title = (it.findtext("title") or "").strip()
        if not title or NOISE.search(title):
            continue
        if skip and skip.search(title):
            continue
        out.append({
            "source": source,
            "title": title,
            "score": None,
            "unit": "",
            "url": (it.findtext("link") or "").strip(),
            "extra": clean(it.findtext("description"), 140),
            "age_h": _age_hours(it.findtext("pubDate") or it.findtext("date")),
        })
        if len(out) >= limit:
            break
    return out


OFFTOPIC = re.compile(
    r"\b(trump|biden|supreme court|election|senate|congress|republican|democrat|"
    r"tariff|ukraine|israel|gaza|meet .* juror|interview with|movie preview|"
    r"401\(k\)|retirement savings|stock market|layoffs|ceo)\b", re.I)


FEEDS = [
    # key,    표시명,      URL,                                             분류
    ("yanko", "제품",   "https://www.yankodesign.com/feed/",              "하드웨어"),
    ("dbm",   "디자인", "https://www.designboom.com/feed/",               "하드웨어"),
    ("d77",   "디자인", "https://www.core77.com/blog/rss.xml",            "하드웨어"),
    ("hack",  "하드웨어", "https://hackaday.com/feed/",                   "하드웨어"),
    ("atlas", "신제품", "https://newatlas.com/index.rss",                 "하드웨어"),
    ("food",  "식품",   "https://www.fooddive.com/feeds/news/",           "산업"),
    ("retail", "리테일", "https://www.retaildive.com/feeds/news/",        "산업"),
    ("cool",  "라이프", "https://coolhunting.com/feed/",                  "산업"),
    ("biz",   "비즈니스", "https://www.fastcompany.com/latest/rss",       "비즈니스"),
    ("tcs",   "투자",   "https://techcrunch.com/category/startups/feed/", "비즈니스"),
    ("platum", "한국",  "https://platum.kr/feed",                         "한국"),
    ("vsq",   "한국",   "https://www.venturesquare.net/feed",             "한국"),
    ("outs",  "한국",   "https://outstanding.kr/feed",                    "한국"),
]

OFFTOPIC = re.compile(
    r"\b(trump|biden|supreme court|election|senate|congress|republican|democrat|"
    r"tariff|ukraine|israel|gaza|meet .* juror|interview with|movie preview|"
    r"401\(k\)|retirement savings|stock market|layoffs|obituary|"
    r"names? .* (chief|ceo|president)|appoints|steps down|"
    r"\uc778\uc0ac|\ubd80\uc784|\uc120\uc784|\ucd94\ubaa8|\ubcc4\uc138)\b", re.I)


# 비전공자가 읽어도 의미 없는 심화 기술 주제는 제외한다
TOO_TECHNICAL = re.compile(
    r"\b(kernel|compiler|linker|bytecode|assembly|allocator|garbage collect|"
    r"cve-?\d|vulnerabilit|exploit|buffer overflow|segfault|"
    r"kubernetes|k8s|docker|terraform|ansible|nginx|postgres|sqlite|redis|"
    r"regex|monad|functor|lambda calculus|type system|borrow checker|"
    r"rust|golang|haskell|erlang|elixir|lisp|scheme|clojure|"
    r"protocol|rfc ?\d|tcp|udp|dns|tls|ssl|http/\d|ipv6|"
    r"driver|firmware|bootloader|risc-?v|x86|arm64|fpga|verilog|"
    r"benchmark|latency|throughput|concurrency|mutex|thread|async|"
    r"repository|commit|merge|pull request|refactor|codebase|"
    r"api wrapper|sdk|cli|daemon|middleware|orm|"
    r"linux|freebsd|unix|beos|distro|package manager|"
    r"neural network|transformer|gradient|quantiz|inference engine|tokenizer|"
    r"defrag|emulat|decompil|reverse engineer)\b", re.I)


def is_accessible(item):
    """제목+설명에 심화 기술 용어가 있으면 제외."""
    text = f"{item.get('title','')} {item.get('extra','')}"
    return not TOO_TECHNICAL.search(text)


def make_feed_source(name, url, limit=6):
    def _fn(limit=limit):
        return _generic_rss(name, url, name, limit, skip=OFFTOPIC)
    return _fn


SOURCES = {

    "hn": src_hn,
    "gh": src_github,
    "lob": src_lobsters,
    "dev": src_devto,
    "ph": src_producthunt,
}
for _k, _n, _u, _c in FEEDS:
    SOURCES[_k] = make_feed_source(_n, _u)

# 소스 → 대분류 (소프트웨어 편중을 막는 데 쓴다)
CATEGORY = {"HN": "소프트웨어", "GitHub": "소프트웨어", "Lobsters": "소프트웨어",
            "Dev.to": "소프트웨어", "PH": "소프트웨어"}
for _k, _n, _u, _c in FEEDS:
    CATEGORY[_n] = _c

# 소스마다 점수 체계가 달라서 그대로 섞으면 GitHub 별 수가 다 이긴다.
# 소스별 가중치로 대략 맞춰 정렬한다.
WEIGHT = {"HN": 1.0, "GitHub": 0.10, "Lobsters": 4.0, "Dev.to": 2.0}
# 점수 없는 소스는 고정 순위값으로 중간에 섞는다
NO_SCORE_RANK = {"PH": 300}
DEFAULT_RANK = 250


# =============================================================== 한국 대조

def keywords(text, n=3):
    text = re.split(r"[—\-–|:]", text)[0]      # GitHub 제목의 설명 부분 제거
    words = re.findall(r"[A-Za-z][A-Za-z0-9]{2,}", text.lower())
    seen, out = set(), []
    for w in words:
        if w in STOPWORDS or w in seen:
            continue
        seen.add(w)
        out.append(w)
        if len(out) >= n:
            break
    return out


def kr_appstore(term, limit=5):
    qs = urllib.parse.urlencode({
        "term": term, "country": "kr", "entity": "software",
        "limit": limit, "lang": "ko_kr",
    })
    try:
        data = fetch_json(f"{ITUNES}?{qs}")
    except Exception:
        return []
    return [{"name": a.get("trackName", ""), "ratings": a.get("userRatingCount", 0) or 0}
            for a in data.get("results", [])]


NAVER_ID = os.environ.get("NAVER_CLIENT_ID", "").strip()
NAVER_SECRET = os.environ.get("NAVER_CLIENT_SECRET", "").strip()


def kr_naver(term, target="webkr", n=3):
    """네이버 검색. 키가 없으면 빈 리스트."""
    if not (NAVER_ID and NAVER_SECRET):
        return []
    qs = urllib.parse.urlencode({"query": term, "display": n, "sort": "sim"})
    req = urllib.request.Request(
        f"https://openapi.naver.com/v1/search/{target}.json?{qs}",
        headers={"X-Naver-Client-Id": NAVER_ID,
                 "X-Naver-Client-Secret": NAVER_SECRET, "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception:
        return []
    return [re.sub(r"<[^>]+>", "", i.get("title", "")) for i in data.get("items", [])]


def gather_evidence(item):
    """LLM이 만든 한국어 키워드로 앱스토어·네이버를 조회해 근거를 모은다."""
    ev = []
    for kw in item.get("kw", [])[:2]:
        for a in kr_appstore(kw, limit=4):
            if a["ratings"] >= 5:
                ev.append(f"[앱] {a['name']} (평가 {a['ratings']})")
        for t in kr_naver(kw):
            ev.append(f"[웹] {t[:60]}")
        time.sleep(0.15)
    seen, out = set(), []
    for e in ev:
        if e not in seen:
            seen.add(e)
            out.append(e)
    item["evidence"] = out[:8]
    return item


def annotate_kr(item):
    if CATEGORY.get(item["source"]) == "한국":
        item["kr"], item["kr_apps"] = "국내소식", []
        return item
    kws = keywords(item["title"])
    if not kws:
        item["kr"], item["kr_apps"] = "판정불가", []
        return item
    hits = []
    for kw in kws[:2]:
        for app in kr_appstore(kw):
            # 오탐 방지: 앱 이름에 키워드가 실제로 있고 실사용 흔적이 있을 때만
            if app["ratings"] >= 10 and kw in app["name"].lower():
                hits.append(app)
        time.sleep(0.12)
    uniq = {a["name"]: a for a in hits}
    item["kr_apps"] = sorted(uniq.values(), key=lambda x: -x["ratings"])[:3]
    item["kr"] = "KR 유사" if item["kr_apps"] else "KR 공백"
    return item


# =================================================================== 출력

def summarize(item):
    """설명이 없는 항목(HN·Lobsters)은 대상 페이지의 메타 설명을 가져온다."""
    if item["extra"] and not re.fullmatch(r"\d+ comments", item["extra"]):
        return item
    url = item["url"]
    if not url.startswith("http"):
        return item
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=8) as r:
            ctype = r.headers.get("Content-Type", "")
            if "html" not in ctype:
                return item
            raw = r.read(120_000).decode("utf-8", "replace")
    except Exception:
        return item

    for pat in (
        r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']{20,})["\']',
        r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']{20,})["\']',
        r'<meta[^>]+content=["\']([^"\']{20,})["\'][^>]+name=["\']description["\']',
    ):
        m = re.search(pat, raw, re.I)
        if m:
            item["extra"] = clean(m.group(1), 140)
            return item

    m = re.search(r"<p[^>]*>(.{40,400}?)</p>", raw, re.I | re.S)
    if m:
        item["extra"] = clean(m.group(1), 140)
    return item


def to_blocks(items, header):
    lines, blocks = [header], []
    for it in items:
        mark = {"KR 공백": "🟢", "KR 인접": "🟡", "KR 유사": "🔴",
                "국내소식": "🇰🇷"}.get(it["kr"], "⚪")
        sc = f" `{it['score']:,}{it['unit']}`" if it["score"] else ""
        cat = f" `{it['cat']}`" if it.get("cat") else ""
        ah = it.get("age_h")
        age = f" ·{int(ah)}h" if ah is not None and ah < 200 else ""
        line = f"{mark} **[{it['source']}]**{cat} [{it['title'][:100]}]({strip_utm(it['url'])}){sc}{age}"
        if it.get("ko"):
            line += f"\n　↳ {it['ko']}"
        elif it["extra"]:
            line += f"\n　↳ {it['extra'][:120]}"
        if it.get("why"):
            line += f"\n　⚖ {it['verdict']} — {it['why'][:40]}"
        elif it["kr_apps"]:
            line += "\n　⚠ 국내 유사: " + ", ".join(a["name"][:20] for a in it["kr_apps"][:2])
        if sum(len(x) for x in lines) + len(line) > 1800:
            blocks.append("\n".join(lines))
            lines = []
        lines.append(line)
    if lines:
        blocks.append("\n".join(lines))
    return blocks


def post_discord(webhook, content):
    payload = json.dumps({"content": content, "flags": 4}).encode()
    req = urllib.request.Request(
        webhook, data=payload,
        headers={"Content-Type": "application/json", "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.status


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--top", type=int, default=30, help="상위 N건만 발송")
    p.add_argument("--only-gaps", action="store_true", help="🟢만 발송")
    p.add_argument("--include-technical", action="store_true",
                   help="심화 기술 항목도 포함 (기본은 제외)")
    p.add_argument("--max-age", type=float, default=72,
                   help="이 시간(h)보다 오래된 항목 제외. 0이면 제한 없음")
    p.add_argument("--sources", default="hn,gh,ph,yanko,dbm,d77,hack,atlas,food,retail,cool,biz,tcs,platum,vsq,outs",
                   help="쉼표로 구분. 생략 시 전체")
    args = p.parse_args()

    items = []
    for key in [s.strip() for s in args.sources.split(",")]:
        fn = SOURCES.get(key)
        if not fn:
            print(f"알 수 없는 소스: {key}", file=sys.stderr)
            continue
        got = fn()
        print(f"  {key}: {len(got)}건", file=sys.stderr)
        items += got

    if not args.include_technical:
        before = len(items)
        items = [it for it in items if is_accessible(it)]
        print(f"  전문 기술 항목 {before - len(items)}건 제외", file=sys.stderr)

    if args.max_age:
        items = [it for it in items if it.get("age_h", 24) <= args.max_age]

    if not items:
        print("수집된 항목 없음", file=sys.stderr)
        return 1

    # 소스별 가중치로 정규화해 정렬
    def rank(x):
        base = (x["score"] * WEIGHT.get(x["source"], 1)) if x["score"] \
            else NO_SCORE_RANK.get(x["source"], DEFAULT_RANK)
        # 시간 감쇠: 하루 지나면 약 55%, 사흘이면 약 35%로 내려간다
        age = max(0.0, x.get("age_h", 24.0))
        return base / ((age + 2.0) ** 0.45)
    items.sort(key=rank, reverse=True)

    # 편중 방지: 소스별 상한 + 대분류(특히 소프트웨어) 상한
    src_cap = max(2, args.top // 8)
    # 분야별 목표 비중 (합 1.0)
    SHARE = {"소프트웨어": 0.25, "하드웨어": 0.25, "산업": 0.20,
             "비즈니스": 0.15, "한국": 0.15}
    cat_cap = {k: max(2, round(args.top * v)) for k, v in SHARE.items()}
    picked, s_cnt, c_cnt, overflow = [], {}, {}, []
    for it in items:
        cat = CATEGORY.get(it["source"], "기타")
        if (s_cnt.get(it["source"], 0) < src_cap
                and c_cnt.get(cat, 0) < cat_cap.get(cat, args.top)):
            picked.append(it)
            s_cnt[it["source"]] = s_cnt.get(it["source"], 0) + 1
            c_cnt[cat] = c_cnt.get(cat, 0) + 1
        else:
            overflow.append(it)
    items = (picked + overflow)[:args.top]

    print(f"{len(items)}건 → 설명 수집 중...", file=sys.stderr)
    items = [summarize(it) for it in items]

    use_llm = llm_enrich is not None and llm_enrich.has_key()
    if use_llm:
        print("한국어 번역 중...", file=sys.stderr)
        items = llm_enrich.translate_batch(items)
        print("근거 수집 중...", file=sys.stderr)
        items = [gather_evidence(it) for it in items]
        print("판정 중...", file=sys.stderr)
        items = llm_enrich.judge_batch(items)
        for it in items:
            it.setdefault("verdict", "불명")
            it["kr_apps"] = []
            it["kr"] = {"없음": "KR 공백", "있음": "KR 유사",
                        "유사": "KR 인접"}.get(it["verdict"], "판정불가")
    else:
        print("한국 대조 중 (단순 매칭)...", file=sys.stderr)
        items = [annotate_kr(it) for it in items]

    if args.only_gaps:
        items = [it for it in items if it["kr"] == "KR 공백"]

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    by_src = {}
    for it in items:
        c = CATEGORY.get(it["source"], "기타")
        by_src[c] = by_src.get(c, 0) + 1
    gaps = sum(1 for it in items if it["kr"] == "KR 공백")
    near = sum(1 for it in items if it["kr"] == "KR 인접")
    header = (f"## 🗓 {today} 아이디어 다이제스트\n"
              f"{len(items)}건 · 🟢 {gaps} / 🟡 {near} / 🔴 {len(items)-gaps-near} · "
              + " ".join(f"{k} {v}" for k, v in by_src.items()) + "\n")

    blocks = to_blocks(items, header)

    if args.dry_run:
        print("\n\n".join(blocks))
        return 0

    webhook = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook:
        print("DISCORD_WEBHOOK_URL 환경변수가 없습니다.", file=sys.stderr)
        return 1
    for b in blocks:
        post_discord(webhook, b)
        time.sleep(1)
    print(f"발송 완료 ({len(blocks)} 메시지)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
